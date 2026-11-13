# Stage 6 contract: the look, the numbers and the words

**Goal: every page and every number Spotter shows reads as finished work. One formatter and one set of text rules sit behind every page and every summary, and tests hold them. Three new reads feed the new pages, and every goal gains five plan-line fields Claude reads. Every page is rebuilt on one design system, agreed before any template changes.**

## A. What stage 6 is, and what it is not

**Stage 6 in three lines.**
- One formatter (`app/formatting.py`) and one set of text rules for every page and every summary, held by tests T1 to T9.
- Three reads the new pages need: a goal's plan line (`metrics.goal_path`), a 28-day grid (`trends.get_days`), cardio bests with a page per activity (`cardio.get_cardio`).
- Every page rebuilt on one dark design system, with a new landing page, a privacy page and designed 404 and 500 pages.

**Not in stage 6.**

| Where it goes | What |
|---|---|
| Stage 7 | A set shape for what Claude says when it uses Spotter, told to Claude in the server instructions and held by eval cases: a clear first line, then short bullets, one fact each, methodical and easy to read, with the link to the page that holds the detail last |
| Stage 7 | The shape of Claude's replies: "How am I doing?" in one call, the basis of "now" named, summaries on `set_goal` and `get_progress`, new bests on an edit, a reps measure for 0 kg lifts in Claude's results (the page's `lifts.history` `reps_only` lands in stage 6), the maintenance line naming its inputs, the stale descriptions, guidance moved out of output-schema notes, deleted entries findable, servings editable, what was read off a photo, pounds converted |
| Stage 7 | A guided start on the words "Set me up": Claude asks, one at a time and each skippable, for a goal, a calorie target and two or three usual foods, and saves them with the existing functions |
| Stage 7 | The `edit_entry` hint and the other hint fixes; evals from 16 to about 41 cases; a chart inside the chat; the thin plugin and two prompts; a public stats page; a picture in the README (the README itself was rewritten on 2026-11-11 as a product page and setup guide for any MCP client) |
| Stage 7 | The text a connected model reads says "the assistant", not Claude: the server instructions, the function descriptions, the refusals and `docs/can-and-cant.md`. It needs an eval run, so it moves with the reply shape |
| Stage 7 | Field names and stored values that say claude: `claude_can`, `logged_by` "claude", the `claude_estimate` tag. A rename is a schema and API change, so it needs an eval run too |
| Stage 7 | `get_progress` for lifts is slow on a long history: 7 to 8 s on three years of daily lifting, older than stage 6. Profile it (`lifts.history`, `metrics.prs`) and fix it only with results proven identical |
| Stage 7 | A README row moves from "From its docs" to "Tried" when that client has been connected to Spotter |
| Later | Staged import, a goal what-if, running distance and bests for Claude, a body fat kind, ads, video |
| Cut | Tile cycling, a demo account with no sign-in, a custom sign-in domain, a downloaded font, a light theme for now, a `get_started` prompt |

## B. The first-use path, walked first

**A stranger goes from the repo to a first sentence. No step asks them to type what Claude or the browser can supply.**

| # | Step | What they see today | What stage 6 changes | How the stage proves it |
|---|---|---|---|---|
| 1 | Finds the repo | The description, the topics and an MIT licence. The README says stages 1 to 5 are live, 18 functions, 17 eval cases, and states no test count | Nothing on the repo page. The README's function count and page list are held against the code (T9). The README's picture is stage 7 | T9 |
| 2 | Reads the README | Since 2026-11-11, a product page and setup guide for any MCP client: why use it, three setup steps that name no assistant, the `/mcp` address, and a table of eight assistants, each "Tried" or "From its docs" | Nothing beyond T9 | T9 |
| 3 | Opens the landing page, signed out | Six numbered items in one card. Sample data is fifth. The first sentence offered is "whoami". No picture, no privacy link, no favicon | One line and one Sign in button. One sample picture from `/static`, with the same exchange as text under it. A Before you start block: what an assistant needs, and the daily cap on new accounts, read from the setting (50 by default). Three steps that name no assistant: sign in, add Spotter to your assistant as a remote MCP server with the `/mcp` address shown, say what you did. Then a short list of assistants that links to the README's table. A Things to say first list. A footer with Privacy and Source code. **Not built in step 4:** the footer's Privacy link, since the privacy page is not in this merge. The exchange's text comes before the picture (as built in step 4, section L) | The suite renders it signed out: the step order, no assistant named in a step, the cap read from settings, no "whoami", the footer links (T3). The owner looks at it signed out: the live walk never signs out |
| 4 | Signs in | The provider's hosted page on its staging address, with no Spotter name. After it, Today. The time zone saves itself from the browser | No code change. Branding the staging page is the owner's click on the provider's dashboard, at no cost. The move to production comes before strangers are invited, and is also his. Landing on Today and the time-zone save stay as they are; in step 4 the save's redirect gains the `saved=time_zone` mark (section L, Motion) | The owner's look at the hosted page. The suite's sign-in tests for Today after sign-in |
| 5 | Adds sample data | An empty Today leads with Add sample data | The landing names it in one line under the steps. On an empty Today, the Today card's one white button is Add sample data | The suite: the button only on an empty account. The live walk presses it, then Remove sample data at the end |
| 6 | Connects an assistant | The address in a box. The claude.ai steps written out by hand. The Claude Code command in a box | Step 2 of the landing names no assistant: add Spotter as a remote MCP server, with the `/mcp` address in a box. Under the steps, a short list of assistants links to the README's table. Two items carry more: Claude Code's holds its command in its box, and Claude's may hold an Add to Claude button, built on the documented link that opens the custom connector dialog with the name and the address filled in. That button ships only if a live click opens the filled dialog before the pages are pushed: before the step 4 push, the lead clicks it on a local build in a signed-in Chrome and stops at the dialog. The address box stays either way | The live walk clicks the button, when the list carries it, in a signed-in Chrome and stops at the filled dialog. It adds nothing. The suite: the address appears whole in one box |
| 7 | Says what they did | "whoami", or a log sentence | "whoami" goes. Offered: "Am I on track for my goals?", "How am I doing?", and one log sentence (section L, Landing page). The reply paragraph (section D) shapes how numbers are said | The eval run (section D). The live walk says "How am I doing?" in a new chat and compares the figures with Progress |

**Sign-in for any client, as of 2026-11-11.**
- The owner switched dynamic client registration on at the sign-in provider on 2026-11-11.
- The authorization server's metadata now carries a `registration_endpoint`, confirmed from its public metadata.
- So a client that registers itself can sign in, as can one that uses a client ID metadata document.
- Tried so far: Claude on the web and Claude Code. Every other README row is "From its docs" (section A, stage 7).

**Known and accepted.** "How am I doing?" took four reads live at stage 5. Stage 6 does not change that call pattern. One call is stage 7.

## C. Number and text rules

**One formatter formats every number on a page and in every summary. Text rules hold every word a person reads. Tests hold both.**

### The formatter, fixed before any package starts

```
app/formatting.py (new). Pure: no database, no clock.
  STEP: {quantity: the shown step, in the quantity's stored unit}
      The one precision table. 0.1 for 1 dp; 1 for whole; 1/60 of a minute for m:ss.
      The widened step is a tenth of it: one more decimal, or tenths of a second.
  Rounding, everywhere in this module: Decimal(repr(value)), quantized ROUND_HALF_UP to
      the step. A quantity in m:ss, and time_gap and time_pace, are in minutes: the
      Decimal is multiplied by 60 first, then quantized to whole seconds, or tenths widened. A value that rounds to zero
      prints "0", never "-0" or "+0".
  rounded(value, quantity, widened=False, step=None) -> float
      For a result field (section D). step overrides the quantity's step. Its one use is
      0.01 for a cardio time goal's should_be_today and ahead_by. With step, the value is
      quantized to step in its stored unit. There is no conversion to seconds.
      A quantity kept in minutes with no step raises ValueError: a result never carries a
      fraction of 1/60.
  is_zero(value, quantity) -> bool
      rounded(value, quantity) == 0. The one rule for "rounds to 0". Nothing else decides it.
  widen([now, plan], quantity) -> bool
      True when now and plan print the same at the shown step and is_zero(now - plan,
      quantity) is False. Close-call rule 2 (section D). It decides nothing else.
  show(value, quantity, page=False, widened=False, *, unit=True) -> str
      For pages and summaries. Rounds to the step, then writes the number and its unit by
      the quantity's rule below. page=True puts a no-break space (U+00A0) before the unit.
      unit=False gives the number alone, for a table cell under a header that names the unit.
      widened=True rounds to the widened step and shows every widened decimal: 80.10, 29:30.0.
      show(None, quantity) raises TypeError. The caller shows words (see the text rules).
      An unknown quantity raises ValueError.
  clock(minutes, widened=False) -> str
      The one m:ss function. Minutes past 59 stay in the minutes: 75 is "75:00".
      metrics.pace, goals.clock and web.clock call it. show uses it for every m:ss quantity,
      and session_time adds the hour from an hour (h:mm:ss).
  field(value, quantity) -> str
      For a form input's value. No separator, no unit, no sign on a positive. It parses again
      with rules.to_float.
  day(d, today) -> str             The D12 form, for today too: "Tue 10 Nov"; "Tue 10 Nov 2025"
                                   in another year.
  day_heading(d, today) -> str     A day heading: "Today, Tuesday 10 November", "Yesterday,
                                   Monday 9 November", else "Sunday 8 November". The year is
                                   added when it is not this year: "Sunday 8 November 2025".
  days(start, end, today) -> str   A range with "to", no weekdays, the month once when both
                                   ends share it: "3 to 9 Nov", "28 Oct to 3 Nov". Ends in two
                                   years each carry their year: "29 Dec 2025 to 4 Jan 2026".
                                   Both ends in one other year: the year once, "3 to 9 Nov 2025".
  day_utc(dt, zone, today) -> str  A UTC time turned into the person's zone, then day(). The one
                                   function for a UTC time to the person's date. For a goal's
                                   closed_at, which goals.html:55 cuts with [:10] today.
  weekday(d) -> str               "Tue". Lane A's first pass.
  weekday_letter(d) -> str        "T". Lane A's first pass.
  Date inputs, for every date function above: a date or datetime, or the ISO string a service
      result carries. One rule.
```

- Every rounding here takes halves away from zero. Python's `round` rounds halves to even, so it is not used for display.
- `app/metrics.py` already rounds some values to 2 dp with `round` before `show` rounds them again. That is accepted: 79.2496 becomes 79.25, then 79.3.
- A pace string at an exact half second can change under the new rounding, since `metrics.pace` used `round` (halves to even). The suite is the judge.
- A half second held as float minutes can print either way: 2.5 seconds can read "2 seconds". Accepted: the Decimal is taken from the float's `repr`.
- The module's rounding never depends on the caller's Decimal context. `STEP` is read-only.
- A gap is the plain float difference of the two unrounded figures. `is_zero` and `widen` are given that same float, and so is `ahead_by` before it is rounded, so the result and the page cannot disagree at a half.
- Quoted figures in this section are format examples, not data.
- The formatter never shows an exponent.
- Jinja gets `show`, `field`, `day`, `day_heading`, `days`, `day_utc`, `weekday` and `weekday_letter` as filters (`web.FILTERS`). The `show` filter defaults to `page=True`. The web never formats a number itself.

**No number format outside the module.**
- T1 fails every numeric format spec (`:g`, `:,`, `:+g`, `:.Nf`, `:02d`) in `app/` outside `formatting.py`.
- Exceptions are named lines in `tests/text_rules.py`: `common.py:217` (the dedupe key), plus each message or fix line lane A keeps. Lane A writes the lines it keeps into `tests/text_rules_kept.py` (new, lane A), and T1 reads that file. It holds one list, `KEPT`, of `(path, the line's text)` pairs, so a kept line is found by its text, not its number. D's pass 2 needs nothing from A's pass 2.
- `rules.kg` goes, in the order section G sets.

### The quantity kinds and the rule for each

| Quantity | Rule | Where it is |
|---|---|---|
| `weigh_in` | As entered, up to 2 dp, trailing zeros dropped | One weigh-in |
| `weight_kg` | As entered, up to 2 dp | A set's weight |
| `bodyweight_derived` | 1 dp, also rounded in the result (section D) | The 7-day average, the smoothed weight, a body weight goal's start, now and plan |
| `estimated_max` | 1 dp, also rounded in the result. A new best and the one it beat are shown widened, together, whenever they print the same at the shown step. `widen` does not decide this case | Every estimated max |
| `reps` | Whole | Reps |
| `kcal` | Whole, comma from 1,000 | An entry, a day total, an average, the machine's number, a cardio estimate, the daily kcal target |
| `kcal_per_serving` | Whole kcal on pages and in summaries. The edit form keeps the stored value (2 dp, `rules.py:55`) through `field` | A saved food's kcal per serving |
| `grams` | As entered (2 dp stored, `rules.py:54`, `:56`), trailing zeros dropped, comma from 1,000 | Grams of a food entry, a saved food's grams per serving |
| `net_kcal` | Whole, comma from 1,000, a minus below zero, never a plus | The day's net kcal: food minus cardio |
| `kcal_left` | "N kcal left", or "N kcal over" below zero. Never a negative "left" | Remaining kcal |
| `maintenance` | Nearest 10, comma, with the estimate tag in its own slot | The maintenance estimate |
| `percent` | Whole, no space: "41%". The share is already whole (`food.py:119`, `trends.py:85`) | The estimated share |
| `met` | 1 dp, no unit | A cardio activity's MET |
| `session_time` | m:ss; h:mm:ss from an hour | One cardio session's minutes |
| `week_minutes` | Whole minutes | Cardio minutes this week and a week over 4 weeks, the weekly cardio target |
| `km` | As entered, up to 2 dp | A distance |
| `pace` | Takes minutes per km as a float. m:ss, staying m:ss past 59 minutes. "per km" in a sentence, "Pace (per km)" as a header | Pace |
| `speed` | Up to 1 dp, trailing zeros dropped, km/h | Speed |
| `incline` | Up to 1 dp, trailing zeros dropped, % | Incline |
| `bpm` | Whole | Heart rate, resting heart rate |
| `cm` | Up to 1 dp, trailing zeros dropped | Waist, hips, chest, arm, thigh |
| `hours` | Up to 2 dp, h | Sleep |
| `steps`, `ml` | Whole, comma from 1,000 | Steps, water |
| `servings` | Up to 2 dp | Servings |
| `count`, `count_avg` | Whole; an average up to 1 dp | Sessions, days logged, the weekly sessions target |
| `rate` | 2 dp, signed, trailing zeros dropped, "0" with no sign at zero | The weight rate a week; a kg, cm or bpm goal's pace a week; a lift's trend a week |
| `weight_change` | 2 dp, signed, trailing zeros dropped | The maintenance window's weight change |
| `time_pace` | Whole seconds a week, signed, the words "seconds a week": "-12 seconds a week" | A cardio time goal's pace |
| `goal_time` | m:ss; widened, m:ss with tenths of a second | A cardio time goal's start, now and plan |
| `goal_target` | As entered, by the goal unit: kg and cm up to 2 dp, trailing zeros dropped; bpm whole; a cardio time in m:ss (stored to 1 dp of a minute) | A goal's `target_value` |
| `time_gap` | Whole seconds and the word: "12 seconds", "1 second"; widened, tenths: "0.5 seconds". No sign: the words ahead or behind carry it | A cardio time goal's gap from its plan line |

**A goal's figures take the goal unit's quantity.** `bodyweight_derived` for a body weight goal, `estimated_max` for a lift goal, then `cm`, `bpm` or `goal_time`. That covers its start, now, plan figure, gap (a cardio time goal's gap is `time_gap`) and `projection.projected`. Its target is `goal_target`.

**Every number, every quantity.**
- No trailing zeros at the shown step: 80, not 80.0.
- A widened figure always shows its widened decimals: 80.10, 29:30.0. m:ss keeps its zeros.
- A JSON number cannot carry a trailing zero, and that is fine.
- A comma from 1,000 on pages and in summaries. Never in a file, a JSON field or a form input.
- One space between a number and its unit. The no-break space goes in only through the Jinja filter (`page=True`). A label built in a service and text inside an SVG chart keep a plain space.
- In a table column of one unit, the unit goes in the header and the cell holds the number. A column whose rows differ in unit (the Trends card's Value, D20; a lift's Best set) holds the number and its unit in one cell.
- A chart's axis ticks: the quantity's shown step, the unit named once, on the top tick, not on each tick.

**Units, as `show` writes them.**
- kg: `weigh_in`, `weight_kg`, `bodyweight_derived`, `estimated_max`, `weight_change`.
- kcal: `kcal`, `kcal_per_serving`, `net_kcal`, `maintenance`. `kcal_left` writes "kcal left" or "kcal over".
- "%" with no space: `percent`, `incline`.
- min: `week_minutes`. km/h: `speed`. h: `hours`. g: `grams`. km, bpm, cm, steps, ml: the quantity of that name.
- No unit: `reps`, `count`, `count_avg`, `servings`, `met`, `rate`, `goal_target` and every m:ss quantity. The caller writes the words: "kg a week", "per km". A cardio time target goes through `goal_time`.
- A template never writes a unit after a value that already carries one. Where a template in section L writes a unit after a value in braces, that value is the number alone (`unit=False`).
- From step 4, a template that writes unit words after a number puts a no-break space (U+00A0) before them, as in a rate line's "kg a week". Step 2's live walk found a plain space there.
- `time_gap` and `time_pace` say "second" only when the printed number is 1.
- `field` keeps the decimals the store keeps, not the shown step, so an edit form never changes a saved value.
- A sign only on a change, and a minus on a figure below zero (`net_kcal`). A total never carries a plus. Zero is "0".
- The minus is the ASCII hyphen-minus.
- Ranges use "to": "20 to 400 kg", the unit once.

### The 21 display picks

| # | Pick |
|---|---|
| D1 | "80 kg": a no-break space between number and unit |
| D2 | "41%": whole, no space |
| D3 | A comma for thousands on pages and in summaries; none in files and inputs |
| D4 | Derived body weight (the 7-day average, the smoothed weight): 1 dp |
| D5 | Estimated max: 1 dp. A new best and the one it beat widen together, also in the result, whenever they print the same at 1 dp |
| D6 | The weight rate a week: 2 dp, trailing zeros dropped, signed |
| D7 | One session's length: m:ss, h:mm:ss from an hour |
| D8 | Weekly cardio minutes: whole |
| D9 | Body sizes up to 1 dp; sleep in hours up to 2 dp |
| D10 | A cardio time goal's pace: whole seconds a week |
| D11 | Maintenance: nearest 10, with the estimate tag |
| D12 | Dates on pages: "Tue 10 Nov" this year, with the year in any other. No ordinals, no comma |
| D13 | Dates in summaries and notes: the page form. ISO stays in data fields |
| D14 | "Reached" is the numbers; "Hit" is the person's mark. "hit (reached)" goes |
| D15 | Remove sample data stays one click, then a "Removed" line, with no undo |
| D16 | The export's CSV files: UTF-8 with a byte-order mark. `export.py` prepends it; `web.export_zip` (`web.py:1341-1347`) writes the text as it gets it |
| D17 | The minus is the ASCII hyphen-minus |
| D18 | The day page's links say "Previous day" and "Next day": no arrows. The two back links that open with a left-arrow entity (`food_edit.html:4`, `progress_lift.html:4`) lose it too, in step 2 |
| D19 | A contrast that removes a real ambiguity stays; a repeated slogan goes |
| D20 | The Trends card is a table: one row per quantity, the value and unit in one column, the window or caveat in a remark column |
| D21 | The server instructions gain a reply paragraph (section D) |

### Dates and times

- Pages and summaries: D12, through `formatting.day`. A day heading names the day in full, through `formatting.day_heading`: "Today, Tuesday 10 November".
- ISO stays in addresses, `<input type="date">`, `datetime` attributes, CSV and JSON fields.
- A person reads times in their own zone. A UTC time is converted before its date is taken: `formatting.day_utc`.
- "Today" and "yesterday" are words, from the person's own date.

### The text rules

| Rule | Holds |
|---|---|
| **No em dash (U+2014) and no en dash (U+2013), anywhere** | Pages, summaries, notes, server instructions, README, docs |
| **No tilde for "about"** | "about" is a word, or an estimate tag |
| **An estimate is a tag in its own slot** | Never brackets, never "of which", never a symbol beside the number |
| **A remark or note sits in its own column or line** | A value cell holds a number and its unit. A table of entries that carry notes has a Notes column. A lift session's notes go on their own line under it, labelled Notes |
| **An empty figure shows words** | A figure Spotter would show but cannot work out yet says why in words ("No weigh-ins yet"). Never a dash, never an invented 0. An optional value the person did not give leaves its table cell blank; a column blank in every row is not shown |
| **Text written for Claude never reaches a page** | A service's `message`, `fix` and `how` never render. A page says what went wrong in its own words, beside the field. A note renders only when it names no field or function |
| No other dash between words | A hyphen joins a compound (weigh-in) or is a minus |
| No arrows, no ellipsis, no curly quotes, no emoji, no exclamation marks | Allowed non-ASCII on pages and in results: U+00D7 between a count and a thing, U+00B7 in page titles, U+00A0 before a unit |
| No "@" or "&" for a word | "3 × 5 at 60 kg", "Roti × 2", on pages and in summaries. A formula in text for Claude keeps the ASCII x |
| No filler, no chat phrases, no listed AI vocabulary | The word lists live in one test data file |
| No semicolons in page text or summaries | Two sentences, or a table row |
| At most 25 words a sentence on a page | |
| Sentence case for headings, labels and buttons | |
| No code words on pages | No snake_case, no function names |
| Pages say "you"; text for Claude says "the person" | A summary uses neither when it can |
| Tags have one spelling each, from one table | Food source, sample, goal status, goal standing, the switch, meal, entry kind |

**Known cases in today's code.** Known at drafting, a floor; T1 and T2 are the judge.
- Summaries joined with "; " become two sentences or a list: `goals.py:643`, `food.py:237`, `:241`, `lifts.py:207`, `entries.py:394`.
- Edit summaries lose the `->` they print and the word "none": `entries.py:346-358`, and `_shown` at `entries.py:199`.
- The sample data summary gives D12 dates and no semicolon: `sample.py:295-296`.

**Shipped in step 2, beyond the cases above.**
- An edit that fills an empty field says "{field} set to {value}". A cleared field says "{field} {value} cleared".
- A before and an after that print the same carry one more decimal, through `common.pair`: in an edit summary and in a replace text. It is the rule a best and the one it beat follow (D5).
- A refused number is echoed as the person sent it (`rules.as_sent`), never rounded to a shown step, and cut by `rules.quote`.
- A new best's text reads "{value}, up from {previous}": "Back Squat estimated max 81.7 kg, up from 70 kg".

**T1 exemptions, named in `tests/text_rules.py`.**
- `links.py:31`: a curly apostrophe stripped from a lift name in a link. No one reads it.
- `rules.py:89`: the U+2026 that marks a cut in `rules.quote`. The quote repeats a value the person or Claude sent, so T2 treats all of `quote`'s output, its cut mark included, as text the person entered.
- `auth.py:148`: three ASCII dots in a log line. No one reads it on a page or in a result.
- `common.py:217`, and each message or fix line lane A keeps (above).
- The byte-order mark constant in `export.py` (D16).

### Tests T1 to T9

| Test | File | What it holds |
|---|---|---|
| T1 | `tests/test_text_rules.py`, the check functions and word lists in `tests/text_rules.py` | Every template's text, read through Jinja's parser with HTML entities decoded; every string constant in `app/` (docstrings and comments skipped); `docs/can-and-cant.md` and `README.md`. Checks: the character allowlist, the word lists, the sentence-shape patterns, no semicolons in templates, no numeric format spec outside `formatting.py`. It skips the exemptions named above and any element with `aria-hidden="true"` and the class `roll`. As built in step 2, `tests/text_rules.py` is the shared module: `check(text, surface)` over seven surfaces (page, title, summary, message, claude, doc, source), one function per rule, and the readers `template_text`, `html_text`, `string_constants`, `format_spec_lines` and `mask_entered`. Negative contractions are not a rule. A listed word that is an attribute name in a markup string is not prose |
| T2 | `tests/test_result_text.py` | Every `summary`, `note`, `message` and `fix` a service returns, on four accounts on a fixed day: empty, sample, edge-case (a zero rate, over target, a day over 1,000 kcal, a cardio time goal, every measurement kind, a new best that widens), and one for the paths those three do not reach. Beside `summary` and `note`, it reads `text`, `label`, `maintenance_note` and `usda_note` as summaries, and `warnings` and `time_zone_note` as messages. In a summary or note: T1's checks, plus no ISO date, no "+0", no trailing ".0" except in a widened figure, a comma on every number of four or more digits except a year inside a D12 date, no semicolon, no "@", no ids, no bracketed words, no "None", "True", "False" or "null" as a word. Messages and fixes get the character allowlist and the word lists only. Text the person entered is exempt: descriptions, notes, names, a name with "(sample)" added (`sample.py:116`), and the output of `rules.quote`. Each call's typed values are masked in that call's texts only (`mask_entered`) |
| T3 | `tests/test_pages_text.py` | Text, from step 2: every GET path in `web.PAGES` with fixture values for each parameter, signed in; `/` and, from step 4, `/privacy`, signed in and out (`/sign-in` only redirects, `web.py:487-495`, so it has no text); from step 4, an unrouted path's 404 and a forced 500; on the same three accounts: T1's checks on visible text, no ISO date outside attributes, no snake_case, sentences of at most 25 words, no "none", "0" or dash standing for an empty figure, confirmations that name their subject, each a line of two words or more, and the thousands and trailing-zero checks on page text. A bare "0" passes only where it is a true count (the Foods table's times logged). The attribution footer's year and licence version are skipped. Until step 4 it skips the rules step 4 lands (see below), and two wordings today's pages keep through step 2: the bare "Saved." (`foods.html:7`, `settings.html:72`) and "whoami" (`home.html:37`, `settings.html:15`). Structure and furniture, from step 4: a Notes column wherever entries carry notes, a remark column in the Trends table, units in headers, estimate tags in their own slot, a column blank in every row not shown, the 404 and 500 pages under the same checks, one render of the message template for each status section L lists, under the furniture checks, the title pattern, a favicon link, link-preview tags on the landing page only. One case on the fixed day: the landing exchange's figures equal `show` of `get_goals` |
| T4 | `app/formatting.py`, `tests/test_formatting.py` | Every quantity's rule, the zero sign, halves by `Decimal(repr(value))` (80.25, 0.15 and 79.05 among them), thousands, "over", m:ss and h:mm:ss, widened decimals (80.10, 29:30.0), `is_zero` and `widen` at exact halves, `show(None, quantity)` raising `TypeError`, no exponent ever |
| T5 | `tests/test_css.py` and `tests/test_page_rules.py` | `test_css.py`, the stylesheet, from step 3: no `word-break: break-all`; value cells `nowrap` and `tabular-nums`; a minimum tap size on each control class; colours only through tokens, with the literal exceptions section L lists; contrast from the token values against an allowed-pairs table in the test (text at least 4.5 to 1, borders at least 3 to 1), with `--text-3` and `--line-strong` banned on `--raised-2`; no `order`, no `dense`, no `*-reverse`, no row-numbered placement; every icon `url()` names a file that exists; on-load motion only under `prefers-reduced-motion: no-preference` and `body:not(.calm)`, and the reduce block turns off transitions, animations and view transitions; `table.rows` stacks below 720 px. The templates, from step 4: every table in a `div.table-wrap` that scrolls sideways, an `inputmode` on every number input, every error tied to its input by `aria-describedby`. `test_page_rules.py`, from step 4, on every GET page on the three accounts: no `style=`, no `<style`, no `data:`, no outside resource; every `svg` has `role`, `title` and `desc`; the only script is `time-zone.js`, under its existing rule; every `popovertarget` names a popover on the page, every popover has a valid `aria-labelledby`, every tile button has a twin link with the same words; fold-outs open only by section L's rules; the tabs follow the Navigation table; `class="calm"` appears exactly when the page is marked; no `li` inside `li.goal`; the icon files and the three favicon files exist, are clean, and answer 200. The SVG namespace attribute, `xmlns="http://www.w3.org/2000/svg"`, is exempt from the outside-address check: it names no resource, and every icon keeps it |
| T6 | `tests/test_export.py` (extended) | Lower snake_case headers; a unit suffix on every fixed-unit number column; a `notes` column wherever the table has notes; no unit in a cell; a text cell that starts with =, +, - or @ carries a leading apostrophe, so it is never read as a formula; the header `avg_hr` is `avg_hr_bpm`; no ".0" on a whole number; ISO dates and UTC times with the offset; the byte-order mark (D16) |
| T7 | `tests/test_consistency.py` | Each figure on both Progress and `get_trends`, and on both Goals and `get_goals`: the page text holds exactly `show(value, quantity, page=True)` of the result's number. On Goals, the comparison sentence is the goal's `plan_line` as written, and the now figure and Plan line today equal the figures in it. Every other figure equals `show` of `get_goals`. The goal chart's description says `plan_line` word for word; its own figures never widen. SVG text and a label built in a service are compared with `page=False`. Step 2: on today's templates, without the five plan-line fields. From step 4: wherever a page shows the plan line, it shows `plan_line` as written |
| T8 | `tests/test_mcp.py`, `evals/` | `INSTRUCTIONS` carries the reply paragraph; from step 3, its `plan_line` sentence too. The eval case in section D |
| T9 | `tests/test_docs.py` | The README's function count, in digits or in words, equals `len(mcp_server.NEEDS)`. Its page list names every top-level page: T9 maps each parameterless `links._PATHS` name except `home` and `sign_in` to its page name (`today` to Today, `foods` to Foods, and so on) and checks the README names it in the page list after "web app"; from step 4, Privacy too. Every eval case count it states equals the number of cases in `evals/cases.json`: lane D writes the count that is true on the combined tree. It writes no test count by hand. D updates the README for `/privacy` in step 4 |

**Build order: T4 first, since every number rule leans on it. Then T1 and T2, then T3's text part, then T6 to T9.** T5's stylesheet part lands in step 3. T3's furniture part and T5's template part land in step 4. All nine are plain pytest tests, so the suite runs them.

**Step 2 does not reshape today's pages. It rewords text only.**
- D18 and the two back links are a change of words.
- In step 2, "none" standing for an empty figure becomes words in place (`day.html:146`, `progress.html:45-46`, `progress_lift.html:10-11`).
- Units in headers, estimate tags in their own slot (`day.html:78`, pinned at `test_web.py:1720-1721`; D11's tag), remark text out of value cells ("(food minus cardio)", `day.html:84`) and hidden blank columns are step 4.
- D20, the Notes columns and remarks moved out of value cells land with the rebuilt pages in step 4, as section L lays them out.
- The Goals page's own error words, in place of the service's `message` and `fix` (`goals.html:66-67`), are step 4, in lane B.
- One more change of words lands in step 2: the goal label's "Bodyweight" becomes "Body weight" (`goals.py:252`, lane A; the example in `mcp_schemas.py:91`, lane C). The pins at `test_sample.py:173`, `:192` and `test_goals.py:572` (lane A) and `test_web_goals.py:322` (lane B) move with it.
- Until step 4, T3 asks for none of the step 4 changes.
- Nothing else in section L lands in step 2. Every markup or shape change there is step 3 or step 4.
- "Saved." and "whoami" stay on today's pages through step 2. T3 skips both until step 4, when section L's words replace them.
- Shipped in step 2: every other confirmation names its subject. "{Kind} deleted." takes its word from one table, `web.ENTRY_WORDS`, and the edit page's "Edit {kind}" is built from it. Goals say "Goal marked as hit.", "Goal dropped." and "Goal reopened.".
- Shipped in step 2, empty figures in words: the cardio estimate cell says "No estimate", with the footnote "No calorie estimate on a row that says No estimate: there was no weigh-in when it was logged. Log a weigh-in and new cardio gets one." A lift with nothing yet says "No estimated max yet" and "No best set yet". The goal form's empty choices say "No lift or activity" and "No measurement".
- Until step 4, the day page's cardio table keeps its "Minutes" header over m:ss cells, and the pace cell keeps " /km".
- `charts.chart_svg` and `charts.bodyweight_svg` take an optional `today`, for the dates they write.

## D. Decimals in what Claude says

**The maths keeps its working precision. Rounding to a shown precision happens once, at a named result field, and nothing computes from a rounded field.**

### Full precision

- `app/metrics.py` keeps every rounding it has: an estimated max to 2 dp (`metrics.py:119`), a 7-day average to 2 dp (`:368`), each smoothed value to 2 dp (`:425`). Comparisons between sessions stay exact.
- `goal_path` (E1) is the one new rounding in `app/metrics.py`. Status, pace, projection, the plan line and the gap are worked out from those values, never from a 1-dp field.
- Chart drawing is exempt: a chart may draw from the rounded fields (`web.py:1098`, `progress.py:182-184`).
- So the fixed-day figures in `tests/test_sample.py` for the maths (the projection, the rate, maintenance, the needed and actual pace) do not change. The fields that round below change their shown value only.

### The named result fields, rounded to 1 dp

| Quantity | Result fields, by key |
|---|---|
| Smoothed weight | `get_trends` `bodyweight.smoothed`, `bodyweight.series[].smoothed` |
| 7-day average | `get_trends` `bodyweight.now`; a body weight goal's `start_value`, `latest` and `projection.latest`; `set_goal` `goal.start_value` of a body weight goal; `log_bodyweight` `trend.now_avg` and `trend.then_avg`; a cardio estimate's `weight_kg` |
| Estimated max | `log_lifts` and `get_log` `new_bests[].value` and `previous` of kind `estimated_max`; `get_progress` `current_estimated_max_kg`, `best_estimated_max_kg`, `estimated_max_over_time[].estimated_max_kg`, `best_set.estimated_max_kg`, `bests[].value` and `previous` of kind `estimated_max`; a lift goal's `start_value`, `latest` and `projection.latest`; `set_goal` `goal.start_value` of a lift goal |

**One rule for every field in the table: 1 dp, whatever its basis. A standalone result field never widens.**
- A goal's `start_value` and a cardio estimate's `weight_kg` are stored with no record of whether they were a 7-day average or a single weigh-in. So no field rounds on its basis.
- A cardio result carries every column of its row, so the same weight is also there as `estimate_weight_kg`. Step 2 left that key at its stored 2 dp beside the rounded `estimate.weight_kg`. Shipped in step 3: `cardio_dict` rounds the stored weight once and gives it under both keys, so they are always equal. The stored column, `estimate()` and `sibling_note` keep the working weight. `test_cardio.py` holds it with 80.25 in the results of log, get_entry, get_log, edit, delete and restore.
- A weigh-in's own `kg`, wherever it appears, stays as entered: it is what the scale said.
- `get_trends` `bodyweight.now` follows the table even when it is the latest weigh-in, so it never prints two ways.
- One pair shows two figures on purpose. Progress' Latest callout is a weigh-in, shown as entered (`weigh_in`). The Trends row is the derived figure at 1 dp. Both show, and T7 accepts the pair.
- So `latest` reads the same in `set_goal`, `get_goals` and `get_progress`, and against `get_trends` `bodyweight.now`. `projection.latest` stays "the same value as the goal's latest" (`mcp_schemas.py:77`).
- Result fields of a cm or bpm goal (`start_value`, `latest`, `projection.latest`) keep the precision they have today.

**One exception: a best and the one it beat widen together, in the result.**
- `new_bests[]` and `bests[]` of kind `estimated_max`: `value` and `previous` are rounded together. When the two would print the same at 1 dp, both are rounded at 2 dp instead.
- `widen` does not decide this case. `metrics.prs` (`metrics.py:165`) already needs the new value strictly greater at 2 dp, so 2 dp always tells them apart.
- The `text` of a new best (`lifts.best_text`) shows the pair the same way, as "{value}, up from {previous}". `lifts.rounded_best` rounds the pair for a result.
- `common.pair` applies the same rule to a before and an after in an edit summary and a replace text.
- Accepted: in that rare case a standalone field in the same result, such as `best_estimated_max_kg`, reads at 1 dp beside the widened pair.

**A goal's two new number fields round at the goal unit's shown step.**

| Goal unit | `should_be_today` | `ahead_by` |
|---|---|---|
| kg (a body weight or lift goal) | 1 dp | 1 dp; 2 dp in the rule 2 case |
| cm | 1 dp | 1 dp; 2 dp in the rule 2 case |
| bpm | Whole | Whole; 1 dp in the rule 2 case |
| min (a cardio time goal) | 2 dp of a minute, as `latest` is today (`goals.py:192`) | 2 dp of a minute, never widened |

- `should_be_today` never widens in the result. The widened plan figure lives in `plan_line` and on the page.
- `ahead_by` is a difference, with no twin in any other result, so it takes the widened step in the rule 2 case.
- `days_ahead` is whole days from `goal_path`. `vs_plan` and `plan_line` are text.

**Where the rounding happens.**
- In the service, with `formatting.rounded`, when the result is built. The page reads the same field, or on Goals the unrounded figure from `goal_charts` through `show`, which prints the same. Claude and the page print the same figure.
- `goal_numbers`, `load_numbers` and `metrics` keep the unrounded values they compute with. The goal fields are rounded in `goals.rounded_numbers` (E1), on a copy. `projection` is a dict that `metrics.projection` returns, so its `latest` is rounded on a copy of it, never in place.
- Two messages keep the goal's start at 2 dp on purpose, `wrong_direction` and `target_is_start`, through `goals.said`: at 1 dp they could contradict themselves.
- `set_goal` `goal.start_value` rounds with the goal numbers' `start_value`: `goals.goal_dict` rounds `start_value` of a body weight or lift goal, so every result that carries a goal row agrees. `entries.entry_dict` uses `goal_dict` for a goal row (`entries.py:93`).
- A cardio estimate's `weight_kg` is rounded only in `cardio_dict` (`cardio.py:68-75`). Never in `estimate()`, never in the stored column: the estimate is stored so it can be worked out again (`cardio.py:10`), and `sibling_note` works from it (`cardio.py:170`).
- Lane A rounds the best set in `lifts.history`'s output, after the loop, into a new dict. The loop reads `estimated_max_kg` back (`lifts.py:127`), and `progress._one` returns that same dict (`progress.py:53`). The web reads it at `web.py:1087`, and `web.py` changes nothing for it.
- `progress.bodyweight_chart` rounds its smoothed and average points (`progress.py:182-184`).
- `progress.bodyweight_chart(weigh_ins, goals, today, since=None)` gained `since` in step 3 and stays the Progress chart's one source. With `since`, it draws only the weigh-ins whose effective date is on or after `since`; `smoothed` is still `metrics.ewma` over every weigh-in, so the line equals `get_trends`'. Fewer than 2 weigh-ins in the window: None, as before. `web` passes `today - 27` days (`BODY_WEIGHT_DAYS`). `test_charts.py` draws the window (lane B); `test_progress.py` holds `since` (lane A). Section E, Beside the three reads, has the detail.
- `show` also rounds to the step, so a page never prints more than the rule, whatever path a figure took.
- T7 covers the best set and the chart's description as well as the Trends and Goals figures.

**Every other field keeps the precision it has today.** The page and every summary show it through `show`. Claude is told to quote the summary where there is one, and from step 3 `plan_line` where a goal carries it.
- So maintenance keeps whole kcal in `maintenance.kcal`, while the page and the summary show the nearest 10 (D11).
- `set_goal`, `get_progress` and `log_bodyweight` have no summary, and `get_goals`' summary carries no figures. Where a result has no figure in a summary, the check in I.2 is the field against the page's `show` of it.

### The close-call rules

**Rule 1. When the gap to the plan line rounds to 0 at the goal's shown step, the page says "On the plan line today", or "At the target" once the line has ended (section L, Goals).**
- `formatting.is_zero` decides it. Nothing else does.
- The result carries `vs_plan` "on the line", `ahead_by` 0 and `days_ahead` 0, and `plan_line` takes the on-the-line template (E1). Once the line has ended or has no length, `days_ahead` is null there as everywhere, and `plan_line` reads "{now} now, at the target."

**Rule 2. When the gap is real but the now figure and the plan figure would print the same, the now figure, the plan figure and the gap all show one more decimal.**
- `formatting.widen([now, plan], quantity)` decides it.
- One more decimal is always enough: a gap that is not `is_zero` at the shown step parts the two figures at the widened one.
- A figure widens only inside a comparison statement: on the page, in summaries and in `plan_line`, through `show(value, quantity, widened=True)`. A standalone result field never widens. Only two cases widen a result field: `ahead_by` in the rule 2 case, except a cardio time goal (see the table above), and a best with the one it beat (the exception above).

**Rule 3. On the plan line, only the now figure prints.** A gap that rounds to 0 can sit between two figures that print differently. The page then shows the now figure and its on-the-line heading, and no plan figure or gap. `plan_line` does the same.

| Goal unit | Shown step | Widened step | Where rule 2 widens |
|---|---|---|---|
| kg, cm | 0.1 | 0.01 | On the page, in summaries and in `plan_line` |
| bpm | 1 | 0.1 | On the page, in summaries and in `plan_line` |
| min (a cardio time) | 1 second | A tenth of a second | On the page, in summaries and in `plan_line`. The data fields keep 2 dp of a minute, as today |

- The sample body weight goal on the fixed day of `tests/test_sample.py` is the rule 2 case, as built in step 3. Now 79.01 against a plan of 78.95663 (79.9 - 2.9 x 27 / 83): both print 79 kg, and the gap is real. `plan_line` "79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind.", `should_be_today` 79.0, `ahead_by` -0.05, `days_ahead` -2, `vs_plan` "behind", tag On track. `test_sample.py` asserts it, worked out a second way.
- The other two sample goals are plain cases: Back Squat "107.9 kg now against 99.8 kg on the plan line today: 8.1 kg ahead." (34 days), Outdoor Run "29:30 now against 29:48 on the plan line today: 18 seconds ahead." (6 days).
- `tests/test_goals.py` builds a rule 1 case and a rule 3 case by hand.
- Accepted: the gap is rounded from the unrounded gap, never taken from the two printed figures. So the printed gap can differ by one step from the printed now less the printed plan: 80.26 against 80.14 prints 80.3 kg and 80.1 kg with a gap of 0.1 kg. `tests/test_goals.py` holds that case, so no second gap figure is ever worked out from rounded fields.
- At the half step the three move together: `vs_plan` "on the line", `ahead_by` 0 and the on-the-line `plan_line` are all true or all false for one goal. `tests/test_goals.py` holds it from both sides of the half step.

### The reply paragraph, added to the server instructions

> How to reply: plain words, one fact a line. Quote each number as Spotter gives it, with its unit: never add decimals, never round further, and never recalculate one yourself. Where a summary gives a figure, say the summary's figure. Say "about" as a word, never as a symbol. No dashes between clauses: use a colon, a comma or a full stop. Name an estimate as an estimate.

- It lives in `app/mcp_server.py` as a named constant, `REPLY_RULE`, joined into `INSTRUCTIONS`.
- Step 2 shipped the paragraph as quoted. Step 3 added one last sentence to it, as shipped: "Where a goal carries plan_line, say it as written, and do not also quote latest, should_be_today or ahead_by." The second half is there because in the rule 2 case `plan_line` carries one more decimal than `latest` does, and a reply must not give two figures for now. The live instructions never name a field no result carries.
- Step 3 also changed the estimate sentence in `INSTRUCTIONS` to "Quote Spotter's numbers; never estimate your own, except a food's kcal, as the food rule says."
- `tests/test_mcp.py` asserts both are in `INSTRUCTIONS`, as it does for the other rules.
- `docs/can-and-cant.md`, as shipped in step 3: its rounding paragraph names `estimate_weight_kg` beside `estimate.weight_kg`, and says a goal's `should_be_today` and `ahead_by` round at the goal unit's step (1 decimal for kg and cm, whole bpm, minutes to 2 decimals), `ahead_by` one decimal finer when latest and `should_be_today` would otherwise read the same, never for a cardio time. Two bullets name the plan line's five fields and say the plan line and the status answer different questions.

### The eval check that holds it

- The judge gains `reply_lacks_pattern`: regular expressions the final reply must not match.
- One new case, `decimals_and_dashes`: "How am I doing?" with weigh-ins seeded so the 7-day average has more than one decimal. The seed, as shipped: two weigh-ins, 79.2 kg yesterday and 79.3 kg today, and no goal, so no correct figure in the reply has two decimals. Expect: `get_trends` called; no write; `reply_lacks` U+2014 and U+2013; `reply_lacks_pattern` a body weight with two decimals (a two- or three-digit kg figure followed by two decimal places).
- The pattern names no figure, so the case holds on any day.
- `tests/test_evals.py` tests the new key on hand-made replies. The judge refuses an empty pattern and one that does not compile.

### The measurement kind words, shipped in step 3

**One table of words for the measurement kinds, defined once. A kind's word never repeats its unit, which stands beside the figure.**

- Home: `app/rules.py`, `KIND_WORDS`, beside `MEASUREMENTS`.
- Read through `app/services/measurements.py`: `measurements.KIND_WORDS` is the same object, and `measurements.kind_word(kind)` returns the word. A kind not in the table gets its name with the underscore as a space, capitalised.
- Readers today: every measurement summary and note, `goals.measurement_name`, and `rules.measurement_value`'s fix text (`KIND_WORDS[kind].lower()`). `goals.NAMES` is gone. The web reads it from step 4 (section L, Today).

| Kind | Word |
|---|---|
| `waist`, `hips`, `chest`, `arm`, `thigh` | Waist, Hips, Chest, Arm, Thigh |
| `steps` | Steps |
| `sleep_hours` | Sleep |
| `resting_hr` | Resting heart rate |
| `water_ml` | Water |

**Texts that changed with it.**

| Text | Before step 3 | Shipped |
|---|---|---|
| `log_measurement` summary, and "Already logged: ..." | "Water ml 2,501 ml on Tue 10 Nov." | "Water 2,501 ml on Tue 10 Nov." |
| The same, resting heart rate and sleep | "Resting hr 59 bpm", "Sleep hours 8.5 h" | "Resting heart rate 59 bpm", "Sleep 8.5 h" |
| A replace note | "Replaced the {day} water ml of ..." | "water", "resting heart rate", "sleep" |
| `edit_entry` `day_taken` message | "already has a water ml measurement" | "a water measurement" |
| `set_goal` refusal of a daily amount | "steps cannot be a goal" | "Steps cannot be a goal", "Sleep ...", "Water ..." |
| `rules.measurement_value` fix, water | "Give water ml in ml, 0 to 20,000, a whole number." | "Give water in ml, 0 to 20,000, a whole number." |
| The same, sleep | "Give sleep hours in h, 0 to 24." | "Give sleep in h, 0 to 24." |
| The same, resting heart rate | "Give resting hr in bpm, 20 to 250, a whole number." | "Give resting heart rate in bpm, 20 to 250, a whole number." |

- The waist, hips, chest, arm, thigh and steps texts are unchanged. `edit_entry` on a measurement's value returns the same fix.
- `rules.measurement_value`'s messages keep the API kind ("water_ml must be a whole number").
- The edge account in `tests/test_result_text.py` pins "Water 2,501 ml", "Resting heart rate 59 bpm", "Sleep 8.5 h" and "Steps 12,346". `tests/test_measurements.py` holds that the table is defined once and that each fix text reads it.

## E. Three service reads

**Each read is worked out in the services, from the same functions the existing reads use. The web never works out a figure.**

### E1. The plan line: `metrics.goal_path`, one pure function, built once

```
metrics.goal_now(kind, points, today) -> float | None
    The goal's "now": goals._now_value, moved here unchanged, so the goal, its start and its
    plan line read one rule.
metrics.goal_now_series(kind, points, today, since) -> [(date, value)]
    goal_now as it stood on each date that has a point, from `since` on, oldest first.
metrics.goal_path(start_date, start_value, target_date, target_value, actual, today,
                  lower_is_better, quantity, *, now=None) -> dict
    Pure: no database, no clock. The one new rounding in app/metrics.py: days_ahead to
    whole days, and the call to formatting.is_zero.
    actual: goal_now_series from start_date, or [] when now is given and no chart needs the
    series. now: the goal's latest; goal_numbers always passes it, since the last point of
    actual can be older than the window "now" reads. Left out, it is the last point of
    actual. quantity: the goal unit's quantity kind
    (bodyweight_derived, estimated_max, cm, bpm or goal_time). "On the line" is
    formatting.is_zero(ahead_by, quantity): the page's own rule, so the two cannot disagree.
    Every number it returns but days_ahead is full precision.
    -> {plan: [{date, value}]        start, today when between the ends, target
        actual: [{date, value}]
        should_be_today: float       the straight line from (start_date, start_value) to
                                     (target_date, target_value), today; the target itself
                                     after target_date
        ahead_by: float | None       up goal: now - should_be; down goal: should_be - now.
                                     Positive is ahead. Full precision
        days_ahead: int | None       ahead_by / the size of the line's change a day, whole
                                     days, halves away from zero. Null when the line has
                                     no length or has ended
        ended: bool                  the plan figure is the target: today is after
                                     target_date, or the line has no length. Worked out
                                     here once; goal_numbers reads it and never works it
                                     out again. Not copied into an MCP result
        vs_plan: "ahead" | "on the line" | "behind" | None
                                     "on the line" when is_zero(ahead_by); else by its sign}
    ahead_by is 0 when vs_plan is "on the line". days_ahead is 0 then too, while the line
    runs; it is null once the line has ended or has no length, on the line or off it.
    days_ahead is also null with no now, and when the line's change a day is 0.
```

**As shipped in step 3, in `app/services/goals.py`.**

```
goals.goal_numbers(goals, loaded, today, charts=False) -> [dict]
    Five fields on every goal, unrounded. charts=True adds path, now_basis and line_ended,
    for goal_charts only. metrics.goal_now_series is built only when charts is True;
    otherwise goal_path gets [] and now=latest.
goals.rounded_numbers(numbers) -> [dict]
    A rounded copy, the five fields included (section D). The numbers given are not changed.
goals.plan_line(now, should, ahead, vs_plan, quantity, ended) -> str | None
    The one sentence, from the unrounded figures. ended is goal_path's ended.
goals.goal_quantity(kind, unit) -> "bodyweight_derived" | "estimated_max" | "cm" | "bpm" | "goal_time"
    The quantity a goal's start, now, plan figure and gap are shown in. A cardio time's gap
    is time_gap. app/charts.py has its own goal_quantity(goal), which takes a goal dict and
    gives the same answer for every goal goal_charts returns.
goals.now_basis(kind, points, today) -> "average_7d" | "latest_weigh_in" | "best_28d" | "latest" | None
    Which measure metrics.goal_now took for latest. None with nothing logged. goals._source,
    the words in messages, reads it, so the two cannot drift apart.
goals.goal_charts(user_id) -> {today,
        goals: [goal numbers, unrounded,
                + path: {plan: [{date, value}], actual: [{date, value}]}   ISO dates
                + now_basis: as above
                + line_ended: bool],                                       goal_path's ended
        includes_sample[, note]}
    | {error, message, fix}
```

- `path.plan` holds the start, then today when strictly between the ends, then the target.
- `rounded_numbers` of `goal_charts`' goals, without `path`, `now_basis` and `line_ended`, equals `get_goals`' goals. `test_goals.py` holds it, and that none of the three is in `get_goals`.
- `goal_charts` is in `services.__all__` and in `test_progress.py`'s `NOT_OFFERED`: no MCP function returns it.

**Where it lands.**

| Reader | What it gets |
|---|---|
| `goals.goal_numbers` | Calls `goal_path` once per goal, with `now=latest`. Adds five fields to every goal's numbers: `should_be_today`, `ahead_by`, `days_ahead`, `vs_plan` and `plan_line`. It keeps every figure unrounded, and builds `plan_line` from the unrounded figures. It reads `ended` from `goal_path` and never works it out again |
| `goals.rounded_numbers(numbers)` | A rounded copy of the goal numbers, by section D, with a rounded copy of `projection`. `set_goal`, `get_goals` and `get_progress` return it, so all three carry the five fields, and `GoalNumbers` in `app/mcp_schemas.py` carries the five, each optional with a null default, described as below |
| `goals.goal_charts(user_id)` (web only) | The shape above. `now_basis` is one of `average_7d`, `latest_weigh_in`, `best_28d`, `latest`, or null with nothing logged, by the rule `goals._source` uses, and is tested in `test_goals.py`. Read through `load_numbers` and `goal_numbers`, so it never disagrees with `get_goals`. The page takes its comparison sentence from `plan_line` as written, never from its own arithmetic (section L, Goals). No MCP function. The series never goes into an MCP result |

**The five fields as `GoalNumbers` describes them** (lane C, `app/mcp_schemas.py`), as shipped. These are what a connected model reads.

| Field | Description |
|---|---|
| `should_be_today` | "Where the plan line is today, in unit: the straight line from start_value on start_date to target_value on target_date. The target once the target date has passed, and the target when target_date is on or before start_date (the line has no length). Otherwise the start before the start date. Rounded to the unit's step: 1 decimal for kg or cm, whole bpm, minutes to 2 decimals. It never carries an extra decimal." |
| `ahead_by` | "How far latest is ahead of the plan line, in unit. Positive is ahead and negative is behind, whichever way the goal points. 0 on the line. Once the target date has passed, or when the line has no length, it is measured against the target. Null when there is nothing logged to compare. Rounded as should_be_today is, with one more decimal when latest and should_be_today would otherwise read the same (never for a cardio_best time)." |
| `days_ahead` | "ahead_by as days of the plan line's change, in whole days. Positive is ahead, negative is behind. 0 on the line while the line runs. 0 also comes with ahead or behind, when the gap is under half a day's change: read vs_plan, not this field, for whether the goal is on the line. Null when ahead_by is null, once the target date has passed, or when the line has no length." |
| `vs_plan` | "ahead, on the line or behind: where latest sits against the plan line, or against the target once the target date has passed or when the line has no length. On the line when the gap rounds to 0 at the unit's step: under 0.05 for kg or cm, under 0.5 bpm, and for a cardio_best time under half a second. It can disagree with status, which compares the projection with the target. Null when ahead_by is null." |
| `plan_line` | "Where the goal stands against the plan line, in one sentence ready to say as written: the now figure, the plan figure and the gap, such as '107.9 kg now against 105.2 kg on the plan line today: 2.7 kg ahead.' On the line only the now figure prints: 'On the plan line today, at 80.3 kg.' When the now and plan figures would otherwise read the same, all three carry one more decimal here. Once the target date has passed, or when the line has no length, it compares latest with the target, such as '76.4 kg now against the target of 75 kg: 1.4 kg behind.', and at the target it reads '78 kg now, at the target.' Null when ahead_by is null." |

- `tests/test_mcp.py` holds the five as optional and described, and the real-services `set_goal` result carries exactly the goal number keys (`set(progress) == GOAL_NUMBER_KEYS`).

**`plan_line`: one sentence per goal, built by `show` with a plain space before a unit.**
- Null when `ahead_by` is null.
- `{now}` is `show` of the now figure, `{plan}` of `should_be_today`. `{gap}` is `show` of the size of `ahead_by`, with no sign: the word says which way. A cardio time goal's gap is `time_gap`.
- It names no days figure. `days_ahead` is its own field.
- `plan_line` is per goal. The `get_goals` summary does not gain it.

| Case | Template | Format example |
|---|---|---|
| Ahead or behind | `{now} now against {plan} on the plan line today: {gap} ahead.` (or `behind.`) | "107.9 kg now against 105.2 kg on the plan line today: 2.7 kg ahead." |
| On the line (rules 1 and 3) | `On the plan line today, at {now}.` | "On the plan line today, at 80.3 kg." |
| On the line once the line has ended or has no length | `{now} now, at the target.` | "78 kg now, at the target." |
| Rule 2 | The first template, with `{now}`, `{plan}` and `{gap}` all through `show(value, quantity, widened=True)` | "80.14 kg now against 80.08 kg on the plan line today: 0.06 kg behind." |

- A cardio time goal in the rule 2 case reads: "29:30.6 now against 29:31.3 on the plan line today: 0.7 seconds ahead."
- After the target date the line has ended, and the sentence reads `{now} now against the target of {plan}: {gap} ahead.` (or `behind.`). A line with no length (the target date on or before the start date) reads the same way: its plan figure is the target too.
- `goal_charts` `now_basis` is null when nothing is logged. The chart's actual line has one point per date that has a point, so it can end before today: the page and the chart take the now figure from `latest`, never from the last point of `path.actual`.
- `tests/test_result_text.py` checks that no walked `plan_line` prints two equal figures beside a gap, on the sample and edge accounts.

**On the page, the two are named apart.** The page's rules are in section L, Goals. In short:
- The comparison sentence is the goal's `plan_line` as written. The page never builds one from its own arithmetic.
- The heading words come from `vs_plan`. `line_ended` picks "the plan line today" or "the target", as `plan_line` does.
- `days_ahead` is whole days, so off the line it is 0 when the gap is under half a day of the line's change. With `days_ahead` 0 or null the page shows the gap alone, with no days figure.
- Accepted: a nearly flat line (a start and a target a few hundredths apart) gives a very large `days_ahead`. No cap.
- The existing tag stays: Reached, On track, Behind, Not enough data. It compares the projection with the target.
- The two can disagree, and both show. The sample body weight goal on the fixed day shows them disagreeing.

**Edge cases.**

| Case | `should_be_today` | `ahead_by`, `days_ahead`, `vs_plan` |
|---|---|---|
| Nothing logged (a goal set from a given start) | The line today | Null; `plan_line` and `now_basis` null too |
| One point | The line today | Worked out: they need no projection |
| The first day (today is the start date) | The start | From now against the start |
| Today before the start date (a time zone change) | The start | Worked out, with no days elapsed |
| Target date passed | The target | `ahead_by` and `vs_plan` against the target; `days_ahead` null: the line has ended |
| Target date on or before the start date (a westward time zone move, then an edit: `_future_date` checks only that the target is after today, `goals.py:317`) | The target | `ahead_by` and `vs_plan` against the target; `days_ahead` null: the line has no length |
| Goal already met | The line today | Positive, "ahead". The tag says Reached, as today |
| Lower is better (a run time, a waist) | The line today | `ahead_by` = should_be minus now |
| A point dated after today | | Counts as today (`metrics.on_or_before`) |

**It must agree with.**
- `goal_now` equals `latest` before rounding, for every kind.
- The plan's ends are the goal's own `start_date`, `start_value`, `target_date`, `target_value`.
- `status`, `needed_per_week`, `actual_per_week` and `projected_date` do not change.

**Tests.**
- `tests/test_metrics.py`: `goal_path` on every edge case above, by hand, up and down, `ended` among them; `goal_now` against the four kinds.
- `tests/test_goals.py`: the five fields through `goal_numbers` and `rounded_numbers` for every kind; rule 1, 2 and 3 cases; each `plan_line` template; a no-length line whose target date has not passed (start tomorrow, target tomorrow or today); the half step from both sides in kg, cm, bpm and a cardio time; the series built only for `goal_charts`.
- `tests/test_sample.py`: the five fields of the three sample goals on the fixed day, to the decimal, worked out a second way.
- `tests/test_mcp.py`: the five keys modelled at every depth.
- `tests/test_web_goals.py`: the two namings apart; both close-call states.

### E2. The 28-day grid: `trends.get_days`

```
trends.get_days(user_id) -> {today, from, to,
    days: [{date, lift_sessions: int, cardio_entries: int, cardio_minutes: float,
            food_entries: int, kcal: int | null, weighed: bool, is_sample: bool}] (28, oldest first),
    weeks: [{to, lift_sessions: int, cardio_minutes: float}] (4, oldest first),
    weight: {latest, high, low} | null, each {date, kg},
    lift_sessions_28d: int, lift_days_28d: int, cardio_days_28d: int, includes_sample[, note]}
```

**As shipped in step 3.**

| Key | Type | Notes |
|---|---|---|
| `today`, `from`, `to` | ISO str | `from` is today less 27 days; `to` is today |
| `days[].date` | ISO str | 28 days, oldest first |
| `days[].lift_sessions`, `cardio_entries`, `food_entries` | int | |
| `days[].cardio_minutes` | float, 1 dp | `round(x, 1)`, as `get_trends` rounds; 0.0 with none |
| `days[].kcal` | int or null | the day's live food entries added up; null with none. 0 is a real figure: food at 0 kcal |
| `days[].weighed` | bool | a live weigh-in that day |
| `days[].is_sample` | bool | any of that day's rows is sample: food, lift session, cardio or weigh-in |
| `weeks[]` | 4, oldest first | `{to: ISO, lift_sessions: int, cardio_minutes: float, 1 dp}`; each the 7 days ending on `to`; the last `to` is today |
| `weight` | dict or null | `{latest, high, low}`, each `{date: the real ISO date, kg}`, over the live weigh-ins of the 28 days; null with none. A tie for high or low goes to the latest in the one order |
| `lift_sessions_28d`, `lift_days_28d`, `cardio_days_28d` | int | a day counts once, by effective date |
| `includes_sample` | bool | over the window's rows only: an older sample row does not count |
| `note` | str, only with sample | `common.SAMPLE_NOTE` |

- No `how`, no `links`, no `summary`.

- A read: no lock, no write limit. Web only, as `list_saved_foods` is. No MCP function.
- The 28 days end today, in the person's zone. Live rows only.
- `kcal` is null on a day with no live food entry. The page shows words for it.
- An entry dated after today counts as today's.
- No data: 28 days of zeros and nulls, `includes_sample` false.
- One sample row in the window makes `includes_sample` true and the note `common.SAMPLE_NOTE`.

**It must agree with.**
- `get_trends`, on the unrounded totals: the last 7 days' `lift_sessions` add up to `sessions.this_week`; `lift_sessions_28d` is the count `get_trends` divides by 4 for `per_week_4w`; the 28 days' `cardio_minutes` add up to the total it divides by 4 for `minutes_per_week_4w`, and the last 7 to `minutes_this_week`. Each is compared as `round(x, 1)`, as `get_trends` rounds (`trends.py:148-151`).
- `weeks`: the last week's `lift_sessions` equals `sessions.this_week` and its `cardio_minutes` equals `cardio.minutes_this_week`, compared as `round(x, 1)`; the 4 weeks' lift sessions add up to `lift_sessions_28d`.
- `weight.latest.kg` equals the last point of `get_trends` `bodyweight.series`.
- `get_log`: a day's `kcal` equals that day's `food.total_kcal`, except when an entry is dated after today. `get_days` counts it as today's; `get_log` lists it as its own day (`log.py:104`).

**Tests.** `tests/test_trends.py`: each count by hand, `weeks` and `weight` by hand with a tie, the empty account, a date after today (a lift and a cardio session today plus one of each after today are one day each), deleted rows out and a restored row back, a day whose only sample row is a lift session, the agreement above. `tests/test_sample.py`: the fixed day's grid: lifts on Monday, Wednesday and Friday; cardio on Tuesday, Thursday and Saturday; nothing trained on Sunday.

**The fixed day's figures, as built** (`tests/test_sample.py`, 2026-11-10).
- Each week: 3 lift sessions. Cardio minutes 86.0, 85.5, 85.0 and 84.5, oldest week first.
- `lift_sessions_28d`, `lift_days_28d` and `cardio_days_28d`: 12 each. Every day is weighed, has food and is sample.
- `weight`: latest and low 78.6 kg on 10 Nov, high 80.1 kg on 16 Oct. No tie.

### E3. Cardio bests: `cardio.get_cardio`

```
cardio.get_cardio(user_id, exercise=None)
  With an activity -> {today, exercise,
      sessions: [{id, date, minutes, distance_km, pace: str | null, pace_min_per_km: float | null,
                  estimate, machine_kcal, notes, is_sample}] (newest first, in the one order),
      bests: {fastest_pace: {pace: str, pace_min_per_km: float, date, entry_id} | null,
              longest: {distance_km, date, entry_id} | null,
              times: [{distance_km, minutes, date, entry_id}] (1, 5 and 10 km, each when reached)},
      week: {minutes, target_minutes}, includes_sample[, note]}
  Without -> {today, activities: [{exercise, sessions_28d: int, minutes_28d: float,
      fastest_pace: str | null, last_date}] (by name, every activity with a live session ever),
      includes_sample[, note]}
```

- Without an activity, `fastest_pace` is the best ever, the m:ss string, and equals that activity's `bests.fastest_pace.pace`.

**As shipped in step 3, with an activity.**

| Key | Type | Notes |
|---|---|---|
| `today`, `exercise` | str | `exercise` is the list's own name |
| `sessions[].id` | int | |
| `sessions[].date` | ISO str | the real date |
| `sessions[].minutes` | float | as stored |
| `sessions[].distance_km` | float or null | |
| `sessions[].pace` | str or null | m:ss from `metrics.pace`; null with no distance |
| `sessions[].pace_min_per_km` | float or null | minutes / distance, unrounded; null with no distance. `show(pace_min_per_km, "pace") == pace` for every session |
| `sessions[].estimate` | dict or null | as `cardio_dict` gives it: `{kcal, weight_kg (1 dp), met, source}` |
| `sessions[].machine_kcal` | int or null | |
| `sessions[].notes` | str | "" when none |
| `sessions[].is_sample` | bool | |
| `bests.fastest_pace` | dict or null | `{pace, pace_min_per_km, date, entry_id}` |
| `bests.longest` | dict or null | `{distance_km, date, entry_id}` |
| `bests.times[]` | list | `{distance_km: 1.0, 5.0 or 10.0, minutes (2 dp), date, entry_id}`, in that order, each only when reached. The page reads the last |
| `week` | dict | `{minutes, target_minutes}` from `cardio.week_minutes`: every activity's minutes in the 7 days ending today |
| `includes_sample` | bool | over this activity's live sessions |
| `note` | str, when present | "No {activity} logged yet." with no sessions; then the sample note |

**Without an activity, the summary form.**

| Key | Type | Notes |
|---|---|---|
| `today` | ISO str | |
| `activities[]` | list, by name | every activity with a live session ever; empty with none, and no note |
| `activities[].exercise` | str | |
| `activities[].sessions_28d` | int | by effective date |
| `activities[].minutes_28d` | float, 1 dp | `round(x, 1)` |
| `activities[].fastest_pace` | str or null | the best ever, from the same `_bests` as `bests.fastest_pace.pace` |
| `activities[].last_date` | ISO str | the real date of the latest session in the one order |
| `includes_sample` | bool | over every live cardio row, however old |
| `note` | str, only with sample | `SAMPLE_NOTE` |

- Exercise None or "" gives the summary form.
- A read, web only. No MCP function. No `how`, no `links`, no `summary`.
- The existing `pace.min_per_km` in `cardio_dict` stays the m:ss string, as before.
- **`cardio.cardio_times(rows, distance_km) -> [(date, minutes, entry_id)]`.** The one rule for a time at a distance, for goals and for bests: a session counts at or over the distance, and its time is its pace x the distance, 2 dp. The rows' order is kept. `goals.cardio_times` is gone: `goals._points` reads `(date, minutes)` from this one.
- **`cardio.sessions_of(s, user, activity, today) -> [CardioEntry]`.** The activity's live rows, newest first in the one order. It returns rows, not dicts: `progress._cardio` and `get_cardio` each build their own dicts from them, since `get_progress`' `pace` is a `{min_per_km, speed_kmh}` dict and E3's is a string. `get_progress`' cardio shape is unchanged. `cardio` never imports `progress`.
- A session with no distance has no pace and is in no best.
- **A tie goes to the earlier session in the one order.** The fastest pace is compared exactly: `cardio.exact_pace(entry)` is `Fraction(repr(minutes)) / Fraction(repr(distance_km))`, so float noise never decides a tie (20.1 min over 4.02 km and 25 min over 5 km are the same 5:00). The output `pace_min_per_km` stays the unrounded float. A best time is compared at its 2 dp.
- **Accepted: a near tie can split.** `fastest_pace` and the 1 km time can name different sessions when two paces differ by less than the 2 dp of a minute a time is rounded to (20.1 min over 4.01 km against 20.2 min over 4.03 km: both show 5:01). `cardio.py`'s module notes say so.
- No sessions: `sessions` empty, every best null or empty, and the note `get_progress` gives ("No Outdoor Run logged yet.").
- A lift's name, or a name not on the list: `unknown_exercise`, `closest` from the cardio names, the fix listing every activity (`match_cardio`).
- The summary form reads the exercise names in one query (`selectinload`).
- A page per activity: `/progress/cardio/{activity}`, link name `progress_cardio`. It shows estimates, so it carries the attribution footer.
- Lane B adds `progress_cardio` to `links._PATHS`, with an `activity` parameter, and its handling in `links.page`, which today formats only `date` and `lift` (`links.py:49-55`). `test_links.py` covers it.

**It must agree with.**
- `get_progress(user_id, activity)`: the same sessions in the same order, the same `week`.
- `cardio_times`: one rule for a time at a distance, for goals and for bests.

**Tests.** `tests/test_cardio.py`: every best by hand, ties, the exact-pace tie against float noise, `pace_min_per_km` unrounded, sessions without a distance, the empty activity, a lift name refused, the agreement with `get_progress` (an Outdoor Run and an activity with machine numbers), the summary form and its `fastest_pace`, a sample session older than 28 days in the summary, a restored session back, `notes` on a session. `tests/test_sample.py`: the fixed day's Outdoor Run bests.

**The fixed day's Outdoor Run, as built.** Four 5 km runs: 31.0, 30.5, 30.0 and 29.5 min, oldest first. Fastest pace 5:54 on Sat 7 Nov. Longest 5 km, a four-way tie, so the earliest, Sat 17 Oct. 1 km 5.9 min and 5 km 29.5 min, both Sat 7 Nov; 10 km not reached. The 5 km time equals the sample goal's `latest`.

### Beside the three reads

**`lifts.history` gains `reps_only`.**
- `reps_only: {lift: {reps, date}}`, for a lift with no live set above 0 kg: the most reps in one set, and the real ISO date of its session.
- **A tie keeps the earlier set in the one order**, as `best_set` does.
- A lift leaves it once any live set of it is above 0 kg. A weighted lift with only sets over 10 reps is in neither `best_set` nor `reps_only`.
- It exists because `best_set` is set only when Epley returns a value, and Epley is None at 0 kg.
- Progress and a lift's page read it as they read `best_set`. Web only: `get_progress` does not copy it, so no MCP result changes.
- The fixed day's Pull-Up: 6 reps, Fri 16 Oct, the first B session.
- Tested in `test_lifts.py`: the tie, a weighted set taking the lift out, 0 kg not counted as weighted, deleted sets out, the real date of a session dated after today.

**`progress.bodyweight_chart(weigh_ins, goals, today, since=None)`.**
- `since` is a plain fourth argument, so a positional call works. Without it, the output is as before stage 6.
- With it, `points` and `average` hold only the weigh-ins whose effective date is on or after `since`.
- `smoothed` is `metrics.ewma` over every weigh-in. So with `since = today - 27` days, `points` equal `get_trends` `bodyweight.series` exactly (tested).
- Each average reads every weigh-in in its 7 days, so the first one in the window can read a weigh-in before `since`.
- `latest` is the last drawn point. Fewer than 2 drawn: None.

## F. Order inside the stage

**Four steps. The second lands on today's pages and is pushed on its own.**

| Step | What | Ends when |
|---|---|---|
| 1 | The design is agreed | Done: the owner agreed the look on 2026-11-11 |
| 2 | The number and text rules land on today's pages: the formatter, every summary through it, the named fields rounded, D18 and the two back links, the reply paragraph, T1, T2, T3's text part, T4 and T6 to T9, the eval case written. No card or table on today's pages changes shape | Done, 2026-11-11 (commit `7acaeb1`): the suite passed plain and with `-W error` on a clean export, pushed on its own, today's pages checked live |
| 3 | The stylesheet and its tokens, the icons, the charts, the three new reads (with E2's `weeks` and `weight`, E3's summary form and `notes`, E1's `now_basis`), the five goal fields, `lifts.history` `reps_only`, `bodyweight_chart`'s `since`; T5's stylesheet part. First, lane D's tightened text checks. Lane C's two instruction changes. Lane A's one table of measurement kind words (section D) | Done, 2026-11-11 (commit `b0ac9b6`, local): the suite passed both ways on the combined tree. Not pushed on its own: it goes live with step 4. The re-check left one must-fix and six smaller items, all carried into step 4 (the table below) |
| 4 | Every page rebuilt as section L lays it out, with D20, the Notes columns and remarks out of value cells, the Goals page's own error words, the address marks on every write, the landing page, the privacy page, the 404 and 500 pages, the favicon and the titles; T3's structure and furniture part and T5's template part (`test_page_rules.py` among it). The pages say "your assistant" where they say Claude today (section L). The Goals page takes its sentence from `plan_line` (section L, Goals). The web reads measurement names from `measurements.kind_word`. A lift with no weighted set shows its `reps_only` figure. The held small fixes below | The exit (section I) |

**Held small fixes for step 4**, each found in step 2's or step 3's live read.

| Fix | Where |
|---|---|
| A sample food stored as "Roti (sample)" (a deleted saved food of the person's already held "Roti", `sample._free_name`) shows the word and the sample tag. It shows the sample mark once | Today, Food card |
| The landing address box wrapped mid-word under the old stylesheet. It stays on one line and scrolls inside itself | Landing page; the walk checks it |
| The day page's cardio table keeps a "Minutes" header over m:ss cells, and the pace cell keeps " /km". It gets its step 4 header and pace cell | Today, Cardio card |
| Remove sample data shows no line after it. It shows "Sample data removed." with no Undo (D15) | Today, the strips |
| Delete my account says "Cardio entries". It takes its words from the one table: "Cardio sessions" (`web.DELETE_COUNTS`) | Delete my account |
| A rate line's unit words follow the number after a plain space. A no-break space goes before them (section C) | Today, Progress, Goals |

**Carried into step 4 from step 3's re-check.** None is live, since step 3 is not pushed. The first is a must-fix and comes before any other page work.

| Fix | Where |
|---|---|
| Must fix. `web._trend` passes `since`, but the body weight card kept step 2's words. With fewer than two weigh-ins in the last 28 days it says "The chart appears from the second weigh-in.", which is false for someone with older weigh-ins, and the 7-day average line goes. The card takes section L's words (Progress, body weight card), keyed on the 28 days. The 7-day average line shows whenever there is a weigh-in. A `test_web` case with weigh-ins older than 28 days holds it | Progress, body weight card (`app/web.py`, `app/templates/progress.html`) |
| No test holds the growth of the tick steps. Pin three outputs: `_ticks(0.5, 9000, KG_STEPS) == [0, 5000, 10000]`, `_ticks(1, 900, LIFT_STEPS) == [0, 500, 1000]`, `_three_ticks(40, 300, KG_STEPS) == [0, 200, 400]` | `tests/test_charts.py` |
| The width estimate is narrow for M, W, m and w, so a "Wed" date or a bpm tick can pass the canvas edge by about 2 units. They get their own width, about 0.9 em, in `_em`. "Wed 27 May" at the right edge and a bpm goal join `WIDE_LABELS` | `app/charts.py`, `tests/test_charts.py` |
| From 960 px up, `svg.chart, svg.chart.small { max-width: none }` removes every cap, so a chart outside the six named contexts has none. Every chart step 4 places sits in a named context, or the rule keeps a base cap | `app/static/style.css` |
| The weight sheet labels its end dates by their real dates, and the other charts by the drawn date. One rule for all: the drawn date | `app/charts.py` |
| A leftover skip guard for `since`, and its docstring line, go | `tests/test_charts.py` |
| "Sleep cannot be a goal" and "Water cannot be a goal" land on `form`, not beside the kind. They are placed by code or field, with the Goals page's other errors | Goals |

**Step 2 carries only words from section L:** the "Body weight" label, D18 and the two back links.

**Step 2's live check was a walk in a signed-in Chrome, run by the lead.** It opened each page in `links._PATHS` signed in, with a date and a lift for the two that take one, and read the text for the step 2 rules. It ran on an account with sample data added and then removed, since an empty account shows almost no figure.

## G. Packages on disjoint files

**Five lanes. Each owns its files across every step, so no two builders edit one file. Nobody edits `tests/conftest.py`.**

| Lane | Files | Waits for |
|---|---|---|
| **A: numbers and services** | `app/formatting.py` (new), `app/metrics.py`, `app/rules.py`, `app/users.py`, `app/services/__init__.py`, `common.py`, `goals.py`, `trends.py`, `cardio.py`, `progress.py`, `lifts.py`, `body.py`, `measurements.py`, `food.py`, `log.py`, `entries.py`, `sample.py`, `account.py`, `export.py`; tests: `test_formatting.py` (new), `test_result_text.py` (new), `text_rules_kept.py` (new), `test_metrics.py`, `test_goals.py`, `test_trends.py`, `test_cardio.py`, `test_progress.py`, `test_sample.py`, `test_lifts.py`, `test_bodyweight.py`, `test_measurements.py`, `test_food.py`, `test_log.py`, `test_entries.py`, `test_export.py`, `test_rules.py`, `test_users.py`, `test_isolation.py`, `test_write_limit.py`, `test_update_settings.py` | Nothing. `formatting.py` is the first thing it hands over |
| **B: web** | `app/web.py`, `app/web_auth.py`, `app/charts.py`, `app/links.py` (gains `privacy`, `progress_cardio` and the repository address as one constant), `app/main.py`, `app/templates/` (every file, and new ones for the cardio page, privacy, 404 and 500), `app/static/` (`style.css`, `time-zone.js`, and new: the 11 files in `icons/`, `favicon.svg`, `favicon.ico`, `apple-touch-icon.png`, the landing picture); tests: `test_web.py`, `test_web_goals.py`, `test_web_account.py`, `test_charts.py`, `test_csrf.py`, `test_links.py`, `test_auth.py`, `test_pages_text.py` (new), `test_css.py` (new), `test_page_rules.py` (new), `test_consistency.py` (new) | A's `formatting.py` and D's `tests/text_rules.py` for step 2. Section L for steps 3 and 4. Builds against the read signatures above, with fakes, until A's reads land |
| **C: MCP surface** | `app/mcp_schemas.py`, `app/mcp_server.py`, `docs/can-and-cant.md`, `tools/mcp_call.py`; tests: `test_mcp.py`, `test_isolation_mcp.py`, `test_calllog.py`, `test_same_account.py` | Nothing. Builds against the shapes above |
| **D: text rules and docs** | `tests/test_text_rules.py` (new), `tests/text_rules.py` (new), `tests/test_docs.py` (new), `README.md` | A's `formatting.py` signatures (T1 checks for formats outside it) |
| **E: evals** | `evals/cases.json`, `evals/judge.py`, `evals/run.py`, `evals/recorder.py`, `evals/issuer.py`, `docs/evals.md`, `tests/test_evals.py` | C's reply paragraph |

- The contract and `BUILD_LOG.md` are the lead's: whoever runs the stage and commits it.
- Untouched by any lane: `tests/conftest.py`, `tests/test_schema.py`, `tests/test_config.py`, `tests/test_usda.py`, `tests/__init__.py`, `app/__init__.py`, `app/models.py`, `app/db.py`, `app/config.py`, `app/auth.py`, `app/calllog.py`, `app/csrf.py`, `app/exercises.py`, `app/services/usda.py`, `evals/__init__.py`, `tools/probe.py`, `docs/contracts/stage-1.md` to `stage-5.md`, `migrations/`. No migration: no column is added.
- T1, T2 and T3 fail on text in every lane. Each lane fixes the text in its own files; D says when T1 passes on the combined tree.
- A T1 failure in a file no lane owns goes to the lead, never to a builder.
- D lists every T1 exemption when it hands over.
- Lane A's step 3 work, all in its own files: `lifts.history` `reps_only`, `bodyweight_chart`'s `since`, E2's `weeks` and `weight`, E3's new shape, E1's `now_basis`, and the `estimate_weight_kg` key of a cardio result rounded as `estimate.weight_kg` is (section D). `formatting.day_utc`, `weekday` and `weekday_letter` are in A's first pass, so the module is written once.
- Every new file section L names sits in lane B: the templates, the static files and `tests/test_page_rules.py`.

**Order of work, step 2. Done, 2026-11-11.** Kept as the record. One-shot builders cannot hand over and then go on, so lane A worked in three passes.

| Step 2 pass | Who | What |
|---|---|---|
| 1 | A, pass 1 | `app/formatting.py` and T4. Hands over the signatures |
| 1 | C | The reply paragraph, and "Body weight" in the label example (`mcp_schemas.py:91`). Starts at once |
| 2 | D, pass 1 | `tests/text_rules.py`: the check functions and the word lists. Hands them over. A's T2 and B's T3 import it |
| 3 | A, pass 2 | Every summary through the formatter, the named fields rounded (`goals.rounded_numbers`, without the five fields), the "Body weight" label, T2, and `tests/text_rules_kept.py` |
| 3 | B | The web through the formatter, D18 and the two back links, T3's text part, T7's step 2 part. In the same pass, B updates `test_charts.py:190-191` to the rounded points and `test_web_goals.py:322` to "Body weight 75 kg" |
| 3 | D, pass 2 | T1, T9, the README |
| 3 | E | The judge key and the eval case, after C hands over |
| 4 | A, pass 3 | Deletes `rules.kg`, after B says that `web.py` and B's tests no longer call it |

- Step 2 closes when the suite passes on the combined tree, plain and with `-W error`.
- `rules.kg` callers today: `web.py:567`, `:646`, `:675`, `:856`, `:970`, `:1013`, `:1114`, `:1121`, `:1159`, `:1173`; `test_web.py:1222`, `:1721` (lane B); `entries.py:201` (lane A, gone in pass 2).

| Step | Order |
|---|---|
| 3 | Done, 2026-11-11, in the passes and fix rounds below |
| 4 | B, after A hands over the reads: every page as section L lays it out. B first traces where a 500 comes from today and says what it found. D updates the README for the privacy page. Before the push, the Add to Claude check (section B, row 6). Then the exit |

**Order of work, step 3. Done, 2026-11-11.** Kept as the record. One-shot builders cannot talk mid-pass: each pass starts from what the passes before it handed over. The plan was three passes. Lane A's pass was too much for one builder, and its files are shared between its halves (`goals.py`, `progress.py`, `test_sample.py`), so it ran as two passes in a row. Each lane then had a fix round after its review; lane A had two, one per pass.

| Step 3 pass | Who | What | Hands over |
|---|---|---|---|
| 1 | D | Tightens the text checks before anything else in the step. `format_spec_lines` catches a nested spec field, a bare zero pad, `.2` on a float, a template held in a module constant, and a `format()` call whose spec is not a constant. Number formatting inside a Jinja template (the `format` and `round` filters) fails T1. `template_text` reads literals in `with` blocks, macro defaults and under attribute access, and the `label` attribute (and `meta content` from step 4). Docs are checked a paragraph at a time. The template sweep is recursive. A stale exemption fails a test, and a kept line exempts only the format-spec rule. `mask_entered` never masks inside a longer number or a date. `app/mcp_server.py` and `app/mcp_schemas.py` are checked under the rules for text Claude reads | The tightened `tests/text_rules.py` and `tests/test_text_rules.py`, with the list of findings in each lane's files |
| 2 | C | After D. Models the five goal fields in `GoalNumbers`, each optional with a null default (`float \| None = None`, `int \| None = None`, `str \| None = None`). Adds the `plan_line` sentence to `REPLY_RULE`. Rewrites the estimate sentence in `INSTRUCTIONS` from "Quote Spotter's numbers; never estimate your own." to "Quote Spotter's numbers; never estimate your own, except a food's kcal, as the food rule says." Updates `can-and-cant`. Fixes D's findings in its own files | The five fields' models and the two instruction changes |
| 2 | B | After D, alongside C. The stylesheet and its tokens, the icons, the charts (against section E's read signatures, with fakes), T5's stylesheet part. Fixes D's findings in its own files | The stylesheet, icons and chart functions |
| 3 | A, pass 1 | After C. E1: `goal_now`, `goal_now_series`, `goal_path`, the five fields, `rounded_numbers`, `plan_line`, `goal_quantity`, `goal_charts` with `now_basis`. The kind words (section D). `estimate_weight_kg` rounded in `cardio_dict`. Fixes D's findings in its own files | E1's signatures as built |
| 4 | A, pass 2 | After pass 1, whose diff was saved first, since both touch `goals.py`. E2 `get_days`, E3 `get_cardio` (both forms) with `sessions_of` and the `cardio_times` move, `lifts.history` `reps_only`, `bodyweight_chart`'s `since` | The reads' signatures as built, for step 4 |
| Fix | B | After its review: margins from the widest label, chart text sizes and width caps, labels that give way, a point after today at today, at most 5 grid lines, 0 kcal as food, counts through `show`, the goal chart's first date and its now from `latest`, the 44 px facts link, card gap and padding, no page-wide clip, `style_version()` safe without the file, a class for a past day. Then each chart drawn from the real reads on the sample account, and the goal chart's desc taking `plan_line` | The hooks in section L |
| Fix | C | After the plan-line review: the five descriptions true in every edge case, `REPLY_RULE`'s last sentence, the `can-and-cant` rounding sentence, the strict key check in `test_mcp.py` | |
| Fix | A, for pass 1 | The ended on-the-line sentence, `ended` from `goal_path` and `line_ended` on `goal_charts`, the series built only for `goal_charts`, the no-length tests, the half-step tests in four units, the up-goal edge cases, the two-equal-figures check, the kind words moved to `app/rules.py` | |
| Fix | A, for pass 2 | The exact-pace tie, tests for seven surviving mutants and a restored row, the fixed day's weight as literals, the summary form's one query | |

- Step 3 closed when the suite passed on the combined tree, plain and with `-W error`.
- A finding of D's in a file no lane owns goes to the lead.
- In step 4, lane B's pages read the kind words through `measurements.kind_word`, and `web.MEASUREMENT_LABELS` goes.

Each builder breaks its own behaviours before handing over and says how many turned a test red.

## H. What must not break

**The security header, as it is today (`app/web.py:197-205`).**

```
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'
Cache-Control: no-store                         (pages; /static: public, max-age=3600, from step 3)
Strict-Transport-Security: max-age=31536000     (when the site is served over https)
```

- It does not change. Anything that would need a change is cut, or goes to the owner first.
- One addition, shipped in step 3, on `/static` only: `Cache-Control: public, max-age=3600` (`web.STATIC_CACHE`), with every security header and HSTS on https. The page cross-fade runs only when the incoming page gets its stylesheet from the browser's cache, and Chrome drops the fade when the stylesheet has to be fetched. Pages keep `no-store`. A `/static` 404 is the plain "Not Found" it was before, with its content type alone and no cache line (as built in step 4: `/static` stays exactly as it was, and `test_web.py` records its answers).
- The stylesheet link carries a version in its query, so a deploy never serves an old stylesheet from cache. As shipped: `web.style_version()` is the first 12 hex digits of the file's SHA-256, read once when the app starts (`web._environment()`), and `base.html` links `{{ style_href }}`, `/static/style.css?v={version}`. With the file missing, `style_version()` logs one line and returns None, and the link is the plain `/static/style.css`: the pages lose their look, and nothing else stops. The address of `/static/time-zone.js` stays exactly as it is: `test_web.py` pins it.
- So after a change to `time-zone.js`, a browser can run the old copy for up to an hour. That is accepted.
- The first move of a visit with an empty cache may not fade. That is accepted.
- Three tests assert the policy exactly: `test_web.py:902-907`, `test_web_goals.py:157`, `test_web_account.py:490`.

**The 404 and 500 pages are lane B's (`app/main.py` is lane B's).**
- The header is added per route (`web.py:1450`, `:1475`). An unrouted path falls through to the MCP mount (`main.py:110`) and gets no page header today.
- After stage 6, an unrouted path gets the designed 404 page with the page header.
- An unhandled error in a page route gets the designed 500 page with the page header. An error under `/mcp` answers as it does now. Lane B traces where a 500 comes from today and says what it found before building.
- `/mcp` and `/.well-known/` answer as they do now.
- With `settings.problem` set (`main.py:83-89`), `/mcp` answers as it does now. The `not_set_up` page (`web.py:341`) takes the message-page frame from section L.

**No inline style and no inline script.**
- No `style=` attribute, no `<style>` element, no inline event handler, no `data:` URL.
- No outside resource: every script, stylesheet, image, font and frame comes from Spotter. A plain link to another site (Add to Claude, Source code, the README's table of assistants) is allowed.
- Variable sizes are SVG geometry attributes or a fixed set of state classes. No SMIL: motion comes from the stylesheet.

**One script.**
- `/static/time-zone.js`, only on a page for a person with no time zone saved. `test_web.py:873`, `:878` and `:891-899` hold this.
- Rolling numbers (section J) change no script: they are the stylesheet only.

**Class names.**

| Held by a test | Kept by choice |
|---|---|
| `card`, `card trends`, `muted`, `inline`, `ok`, `error`, `link`, `link-box`, `steps`, `entry-line`, `goal`, `attribution`, `meal empty`, `notice sample-banner`, `notice goal-note`, `tag tag-sample`, `tag tag-you_said`, `tag tag-saved_food`, `tag tag-claude_estimate`, `tag-good`, `tag-behind`, `chart`; in charts: `grid`, `goal`, `projection`, `axis`, `series smoothed` | `primary`, `danger`, `tag-usda`, `tag-status`, `ok undo`, `facts`, `entries`, `goal-label`, `dot`, `weigh-in`, `series`, `tag-estimate`, `top-out`, and the component classes section L's Stylesheet names |

**Test-held ids and structures.** Known at drafting, a floor; the five web test files are the judge.

| Held | Where |
|---|---|
| Ids: `claude-code-command`, `auto-time-zone`, `time-zone`, `measurement-kind`, `cardio-exercise`, `bodyweight-chart`, `progress-chart` | `test_web.py:179`, `:861`, `:934`, `:1163`, `:1700`, `:1225`, `:1231` |
| Ids on the goal form: `goal-kind`, `goal-measurement`, `goal-exercise`, `goal-distance`, `goal-target`, `goal-date`, `goal-start`, `goal-text`; and `goal-N` on a goal | `test_web_goals.py:91-96`, `:220-222`, `:77` |
| The `"\n    <li>"` split inside `<ol class="steps">` on the landing page | `test_web.py:174-175` |
| `<dt><a href="{path}">` on the signed-in landing, for `/today`, `/progress`, `/goals`, `/foods`, `/settings` | `test_web.py:208-209` |
| `<code class="link-box" id="claude-code-command">` holding the Claude Code command | `test_web.py:179` |
| `data-time-zone="missing"`, and `<form id="auto-time-zone" method="post" action="/settings" hidden>` with its hidden inputs | `test_web.py:860-865` |
| `rel="prev"` and `rel="next"` on the day links | `test_web.py:1197`, `:1202` |
| `<div class="meal empty">\n    <h3>Dinner</h3>`, and the `<div class="meal( empty)?">` pattern | `test_web.py:1717`, `:1696` |
| `<footer class="attribution">` | `test_web.py:1746` |
| `<li class="(?:goal\|entry-line)" id="goal-N">` | `test_web_goals.py:77` |
| `<form method="post" action="/goals/N/status" class="inline">`, its two hidden inputs and `<button class="link">`. Held until step 4, when Hit becomes a `secondary` button | `test_web_goals.py:125-128` |
| `<div class="error"><p>message</p><p>fix</p></div>` beside a goal form field. Held until step 4, when the page's own words replace it | `test_web_goals.py:255-264` |
| `<li class="entry-line"><span>name</span><strong>n</strong></li>` on the delete page | `test_web_account.py:321` |
| The 404 body: "There is nothing here." and the text after `<main>` | `test_web.py:1241-1242` |
| In charts: the projection's `stroke-dasharray`, the `<circle>` dots each with a `<title>`, the `<title>` and `<desc>` of each chart | `test_charts.py:35-42`, `:94-95` |

- A rewrite that changes one says so when it hands over, old next to new, with the meaning kept.

**Address marks, step 4.** Every web write's redirect carries a mark (`saved=`, `deleted=` or `restored=`), and `/today` passes it on to the day address (`web.py:598` drops it today). Section L's Motion lists the writes.

**Assertions that change in step 4 with the same meaning.** Known at drafting, a floor; the suite is the judge.

| What changes | Pins |
|---|---|
| A write's redirect gains its mark | `test_web.py:1268`, `:1321`, `:1338`, `:1376`, `:1449`, `:1787`, `:1855`, `:2120`; `test_web_account.py:82`, `:110` |
| The goal form shows one kind at a time | `test_web_goals.py:82-101` |
| Hit is a `secondary` button | `test_web_goals.py:125-128` |
| The nav anchor gains classes and `aria-current` | `test_web_goals.py:159` |
| The signed-in landing steps | `test_web.py:206` |
| The signed-out landing's six steps become three that name no assistant; the command box and the address stay on the page | `test_web.py:183-199` (`test_home_walks_a_newcomer_through_setup_in_six_steps`), `:207` |
| Page lines say "your assistant" where they say Claude | `test_web.py:872` (the message page), `:1269` (the Sets card), `:1515` (the switch card) |
| "Saved." becomes "Saved food saved." | `test_web.py:1924` |
| The Goals page's own error words | `test_web_goals.py:255-264` |

- Still held: `link-box` and `id="claude-code-command"`, the `"\n    <li>"` steps split, and `<dt><a href="{path}">` for the five pages.

**The tests today, and what is expected to change.**
- The five web test files are `test_web.py`, `test_web_goals.py`, `test_web_account.py`, `test_charts.py` and `test_csrf.py`.
- Every list of pins below is known at drafting, a floor; the suite is the judge.

| What | Count or pins |
|---|---|
| Today | 1,598 collected cases from 968 test functions |
| The five web test files | 434 cases from 260 functions |
| Step 2: an ISO date or a 2-dp figure in page or summary text | `test_web_goals.py:109`, `test_sample.py:192`, `test_web.py:1233`, `test_web_goals.py:437`, `:486`, `test_charts.py:106` |
| Step 2: "none" for an empty figure | `test_web.py:1755` (`<span class="muted">none</span>`) |
| Step 2: the "you" rule in a summary | `test_sample.py:349` ("your own") |
| Step 2: a 2-dp literal of a field section D rounds | `test_sample.py:175`, `:180-181`, `:200`, `:203`, `:325`; `test_goals.py:163-164`, `:284`, `:307`; `test_progress.py:139`, `:184`; `test_trends.py:49`, `:53`; `test_bodyweight.py:56`; `test_lifts.py:99` |
| Step 2: a rounded field compared with `epley(weight, reps)`, which is 2 dp | `test_goals.py:173`, `:174`, `:176`; `test_progress.py:53`, `:54`, `:71`, `:99`, `:114`, `:131`; `test_lifts.py:115`, `:123`, `:368`; `test_update_settings.py:223-225`, `:228`, `:236`, `:296` |
| Step 2: the no-break space (D1) | Every page assertion with a plain space between a number and its unit. A label built in a service and text inside an SVG chart keep a plain space |
| Step 2: shape | No table or card changes shape in step 2 |
| Step 4 | Many of the web test functions; the suite is the judge. The Trends card table (D20) replaces the markup `trends_card()` reads, at its 6 call sites in `test_web_goals.py`; the day page's totals and notes markup moves too. Helpers first, one commit per helper |

- Lane A's rounding at `progress.py:182` turns lane B's `test_charts.py:190-191` red: it compares the chart's smoothed values with `metrics.ewma`. Lane A's "Body weight" label turns lane B's `test_web_goals.py:322` red: it checks "Bodyweight 75 kg". B updates both in its own step 2 pass, to the rounded points and to "Body weight 75 kg".
- Every changed assertion keeps its meaning. The hand-over lists each one.

## I. Exit

1. The full suite passes, plain and with `-W error`, on a clean `git archive` export of the commit to be pushed.
2. A live walk of section B in the owner's signed-in Chrome by an agent that never signs in and never presses Delete account. It adds sample data, reads Today, Progress, Goals and one cardio page (**not built in step 4:** an activity's page), clicks Add to Claude to the filled dialog and stops (when the landing's list carries the button), says "How am I doing?" and "Am I on track for my goals?" in a new chat and compares the figures with the pages, then removes the sample data. It lists CSP violations from the console (expect none).
   - It walks at 360, 390 and 1,280 px wide, on the owner's account empty and then with sample data. The suite covers the edge account's pages (T3, T5, `test_page_rules.py`).
   - **The walk's checks on the look**, each at every width unless named, are in the table after this list.
   - It checks whether the cross-fade runs after a form post (either outcome is accepted), and that the eval line shows on the live landing page.
   - It assumes an empty account, or Add sample data refuses with `not_empty` (`sample.py:261`).
   - It assumes Spotter is already connected in claude.ai.
   - It skips the cardio page if K1 cut it.
   - Where a result has no figure in a summary, the comparison is the field against the page's `show` of it.
   - For the plan line, it compares the page's statement with the goal's `plan_line`.

**The walk's checks on the look (exit item 2).**

| Check | How |
|---|---|
| No page scrolls sideways | `document.documentElement.scrollWidth` against the window's width on every page. Nothing hides overflow now: `body` has no `overflow-x: hidden` |
| Each width-capped chart sits well in its card | At 1,280 px: the body weight, lift, goal, pace and Today pair charts, each narrower than its column by design |
| Chart labels fit in fonts other than Segoe UI | The label widths are estimates. The walk reads each chart's text boxes against the canvas in the browser's own font, and on the owner's phone (Roboto or SF) |
| A sheet's chart drawing in on each open reads well | Open and close each Today sheet twice |
| Chart text shows at 11 to 14 px | The computed size of each chart's text times its drawn scale, at each width |
| The landing address box stays on one line | It scrolls inside itself, never wraps mid-word |

3. One eval run of every case, pinned to `claude-opus-5-5`: about $7 at list price, an estimate. It runs only on the owner's okay. The pass rate is given as it is.
4. Live after the push: `/health` 200, `/mcp` 401 without a token, the landing page 200.

## J. Open calls that are the owner's

| Call | Options, the pick first |
|---|---|
| Rolling numbers | **A digit roll in the stylesheet only, on the Today card's one big number and nowhere else, kept only if the digits line up on the owner's phone**; dropped otherwise. It lined up in desktop Chrome. No script. The real number stays in the page as plain text. The rolling digit columns carry `aria-hidden="true"` and the class `roll`. That marker is how T1 and T3 skip them |

- Whether `edit_entry` is marked destructive belongs to stage 7 and is not decided here.
- A known name left alone: `pace.min_per_km` in MCP results stays the m:ss string in stage 6.

## K. Cut order if the stage runs long

1. The cardio activity page (E3's page; the read can stay). If cut, Progress' Cardio card names each activity with no link.
2. Rolling numbers.
3. The page cross-fade.

---

## L. The look, page by page

**Every page is rebuilt on one design, laid out below. Section C's number and text rules and section H's limits hold on every page.**

- **Every page says "your assistant" where it says Claude today.** The quoted words below are written that way. A line this section does not quote follows the same rule: the Settings data card's "These are yours to do, here. Claude does not do them for you." becomes "your assistant", and so does the signed-in landing's line for Settings. The rule and what it leaves alone are under Landing page. T3 checks that no visible page text says Claude outside the landing's list of assistants. Text a service writes for the model never renders on a page (section C), so it cannot trip that check; a service note a page renders that names Claude goes to the lead.
- Link names: the pages in `links._PATHS`, plus `privacy` and `progress_cardio`. The edit pages, Delete my account, and the error and message pages keep no link name: they are reached from a page, never handed out by a service.
- Every page here with its own address has a route in `web.routes`, a title (Favicon and titles), lane B as its owner, and a place in T3. `/privacy` is routed beside `/`, open signed in and out; `/progress/cardio/{activity}` joins `web.PAGES`. A message page has no address of its own: it answers a request that failed.

### Stylesheet and tokens

**One stylesheet, dark only, the system font, every colour a token. Contrast is worked out from the tokens and held by T5. Nothing on a page sizes or colours itself.**

The "Proved by" columns in these sections use four names.
- **WEB**: the three web test files, `tests/test_web.py`, `tests/test_web_goals.py` and `tests/test_web_account.py`.
- **`test_page_rules.py`**: `tests/test_page_rules.py`, lane B's new file of markup rules, part of T5.
- **Live walk**: exit item 2, at 360 px, 390 px and 1,280 px wide.
- **Owner's look**: the owner, on his phone and on a laptop.

#### The rules

| Rule | Proved by |
|---|---|
| One file, `app/static/style.css`, linked once per page with the version query of section H. No other stylesheet, no `<style>` element, no `style=` attribute | `tests/test_page_rules.py` |
| Dark only. `color-scheme: dark` on the root. Every page head carries `<meta name="color-scheme" content="dark">` and `<meta name="theme-color" content="#000000">`. No light theme and no `prefers-color-scheme` query | T5, `test_page_rules.py` |
| The system font: `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif`. The address and command boxes use `ui-monospace, "SF Mono", Menlo, Consolas, monospace`. No `@font-face`, no font file | T5 |
| Numbers use tabular figures: `font-variant-numeric: tabular-nums` on the body | T5 |
| Every colour is `var(--token)` from the token table. Written literally only: `transparent`, and black with an alpha in shadows, the sheet's backdrop (62%) and the laptop top bar (82%) | T5 |
| Every `url()` names a file under `/static/icons/` that exists. No `data:`, no `http:`, no `https:` | T5 |
| No reordering. No `order` property, no `grid-auto-flow: dense`, no `row-reverse` or `column-reverse`, no `grid-row` or `grid-area` that names a row line. A row `span` is allowed | T5 |
| Text sizes in px, inputs at 16 px, so a phone never zooms on focus | T5 |
| No page-wide clip: `body` carries no `overflow-x: hidden`, so the walk sees any sideways overflow | T5 (`test_no_page_wide_clip_hides_sideways_overflow`), live walk |
| `:has()` is used only for cosmetic touches: the footer padding, the chosen radio card's outline, and an optional step's dashed number (the three-step landing has no optional step, so that rule marks nothing). Layout keys on a class the server sets | T5 (`test_has_is_only_ever_cosmetic`) |

#### Hooks the pages must use, as built in step 3

**The stylesheet places and sizes by these names. Step 4's templates carry them exactly.**

| Hook | Names | What keys on it |
|---|---|---|
| Page classes, on `main.page` | `today`, `progress`, `goals-page`, `cardio-page`, `lift-page`, `foods-page`, `settings-page`, `landing` | Each page's laptop grid. `goals-page` has no rule yet: Goals lays out by `div.goals-main` |
| A past day | `main.page.today.past`, also for a day after today | The Today card spans both columns, with Food on the left and `div.col-side` on the right |
| Width classes, on `main.page` | `form-page` (640 px), `notice-page` (480 px), `privacy-page` (720 px) | The one centred column of the edit pages, the message pages and Privacy |
| Card classes | `bw-card`, `cardio-card`, `lifts-card`, `pace-card`, `bests-card`, `saved-foods`, `save-food`, and `hero` for the Today card | Laptop placement |
| Laptop placement selectors | `.progress .bw-card` (columns 1 to 7), `.lift-page .chart-card` (1 to 8), `.cardio-page .pace-card` (8 to 12), `.goals-main` (1 to 8) | The chart sizes are worked out from these spans |
| Chart ids | `#bodyweight-chart`, `#progress-chart`, `#chart-pace` | Each keeps its chart function's default id. Laptop text size and width cap |
| Chart contexts | `li.goal svg.chart`, `.sheet svg.chart`, `.glance svg.chart.small` | Goal charts sit inside `li.goal`, sheet charts inside `.sheet`, the Today pair inside `div.glance`. A chart outside these gets the phone size and the phone cap |
| Rolling digits | `roll`, with `aria-hidden="true"` | Motion, section J |

- `tests/test_css.py` reads the spans and caps above to work out each chart's text size. A template that moves a chart out of its context breaks the size.

**Order on screen.** Sort a page's blocks by their top edge, then their left edge. That order equals the markup order, at both widths. The tab order follows the markup.
- A two-column page places whole wrappers, never single cards across wrappers: on Today `div.glance`, `div.col-main` and `div.col-side`; on Goals `div.goals-main` beside `aside.set-goal`.
- Proved by T5's ban above, and the live walk of every page at both widths on the owner's account, empty and then with sample data (section I). The suite covers the edge account's pages. On Today the order holds by the cards' heights, so the walk checks it there in both states.

#### Navigation

**Signed in, every page carries the same four tabs: Today, Progress, Goals, Settings. A floating pill bar at the bottom on a phone, a top bar on a laptop.**

| Width | What shows |
|---|---|
| Phone | A pill bar floating 12 px above the bottom edge plus the safe area, centred, `--bar` at 90% with a blur, a 1 px `--line` border. Four tabs, each its icon over its label, each at least 76 px wide and 52 px tall. No brand, no Sign out |
| Laptop | A top bar that stays at the top while the page scrolls: the brand at the left, the four tabs as one pill group, Sign out at the right. Black at 82% with a blur, a 1 px `--line` rule under it. Each tab its icon beside its label, 44 px tall |
| Signed out | A header with the brand and a plain "Sign in" link, when web sign-in is set up. No tabs |

| Pages | Current tab |
|---|---|
| Today, any day page, an entry's edit page | Today |
| Progress, a lift's page, an activity's page | Progress |
| Goals | Goals |
| Settings, Foods, a saved food's edit page, Delete my account | Settings |
| The landing page, Privacy, 404, 500, every message page | None |

- The current tab carries `aria-current="page"`. No other tab does.
- Foods is reached from the Food card's "Saved foods" link and from Settings.
- Proved by `test_page_rules.py`: every signed-in page has the four tab links in that order, and `aria-current` on the one the table names, or on none. The look is the owner's.

#### Colour tokens

| Token | Value | Used for |
|---|---|---|
| `--bg` | `#000000` | The page |
| `--surface` | `#161616` | Cards, tiles on the page |
| `--surface-hover` | `#1b1b1b` | A tile on the page, hovered |
| `--raised` | `#232323` | Chips, inputs, tiles inside a card, inner boxes, strips |
| `--raised-2` | `#2c2c2c` | A tile inside a card, hovered; a secondary button, hovered; the "You" bubble on the landing page |
| `--sheet` | `#1c1c1c` | A sheet and its sticky head |
| `--bar` | `#1a1a1a` | The phone tab bar, drawn at 90% opacity |
| `--line` | `#343434` | Dividers only. Never the only mark of anything |
| `--line-strong` | `#707070` | Input borders, dashed "not yet" outlines, tag outlines, the plan line, the empty pill |
| `--text` | `#f2f2f2` | Main text, the white button, filled pills, chart lines |
| `--text-bright` | `#ffffff` | The white button, hovered |
| `--text-2` | `#a6a6a6` | Secondary text, labels, units, bars |
| `--text-3` | `#8c8c8c` | Small labels, notes, axis text |
| `--on-primary` | `#000000` | Text on the white button and the danger button |
| `--up` | `#4ade80` | On track, reached, the saved line's dot |
| `--today` | `#5aa9ff` | Today's ring in the day grid, the today line on charts |
| `--warn` | `#f5b841` | The estimate tag, behind |
| `--err` | `#ff7a7a` | Error text and borders, the danger button |
| `--sample` | `#a9b4ff` | The sample tag and the sample banner |

- Colour never carries meaning alone. Every coloured tag has words. Every pill state is a shape and has hidden words.
- Green means good news only. Blue means today only. Amber means an estimate or behind.

#### Contrast, worked out from the tokens

WCAG 2 relative luminance. Text needs at least 4.5 to 1. A border or mark that carries meaning needs at least 3 to 1.

| Foreground | On `--bg` | On `--surface` | On `--raised` | On `--sheet` | On `--raised-2` |
|---|---:|---:|---:|---:|---:|
| `--text` | 18.76 | 16.16 | 14.04 | 15.22 | 12.47 |
| `--text-2` | 8.63 | 7.43 | 6.46 | 7.00 | 5.74 |
| `--text-3` | 6.25 | 5.38 | 4.67 | 5.07 | **4.15, not allowed** |
| `--up` | 12.05 | 10.38 | 9.02 | 9.78 | 8.01 |
| `--today` | 8.55 | 7.37 | 6.40 | 6.94 | 5.69 |
| `--warn` | 11.82 | 10.18 | 8.84 | 9.59 | 7.86 |
| `--err` | 8.32 | 7.17 | 6.22 | 6.75 | 5.53 |
| `--sample` | 10.66 | 9.19 | 7.98 | 8.65 | 7.09 |
| `--line-strong` (border) | 4.24 | 3.65 | 3.17 | 3.44 | **2.82, not allowed** |
| `--line` (decorative) | 1.69 | 1.45 | 1.26 | 1.37 | 1.12 |

| Other pair | Ratio |
|---|---:|
| `--on-primary` on `--text` (the white button) | 18.76 |
| `--on-primary` on `--text-bright` (hovered) | 21.00 |
| `--on-primary` on `--err` (the danger button) | 8.32 |
| `--text-2` on `--bar` at 90% over `--text`, the lightest thing that can pass under the tab bar | 5.42 |
| `--text-2` on `--surface-hover` | 7.08 |
| `--text-3` on `--surface-hover` | 5.12 |

- **Two pairs are banned.** `--text-3` never sits on `--raised-2`. `--line-strong` never borders `--raised-2`. So a tile inside a card shows its note in `--text-2` while hovered.
- `--raised` against `--surface` is 1.15 to 1. A raised fill is a background only, never the only sign of a state.
- T5 holds the allowed pairs as a table in the test file and checks each ratio from the token values. The owner's look checks that the stylesheet uses only listed pairs.

#### Radii

| Thing | Radius |
|---|---|
| Card | 24 px |
| Tile | 20 px |
| Sheet | 28 px; the top corners only on a phone, every corner on a laptop |
| Inner box: a strip, a notice, a standing box, a callout, the sheet's chart panel | 16 px |
| Input, address box | 14 px |
| Button, chip, tag, tab, tab bar, day pill, segment | Fully round |

#### Spacing and widths

| Measure | Phone, under 960 px | Laptop, 960 px and up |
|---|---|---|
| Side gutter | 16 px | 32 px |
| Gap between cards | 12 px | 16 px |
| Card padding | 20 px | 20 px; 24 px on a goal card |
| Content width | Full | At most 1,120 px, centred, on a 12-column grid |
| Room under the content | 104 px plus the safe area, for the floating tab bar | 40 px |

- Row tables stack into label and value pairs below 720 px. That is the only other breakpoint.
- No page scrolls sideways at 360 px wide. A wide table scrolls inside its wrapper. Nothing clips the page to hide it. Proved by the live walk at 360 px.
- Chart cards take the card gap and card padding: `.pair { gap: var(--gap) }`, `.chart-card { padding: var(--pad) }`.

#### Type

| Element | Phone | Laptop | Weight |
|---|---:|---:|---:|
| Page title (h1) | 30 px | 34 px | 700 |
| Day line under the title | 17 px | 17 px | 500 |
| The Today card's number | 64 px, unit 24 px | 64 px | 700 |
| A sheet's number | 64 px, unit 26 px | 64 px | 700 |
| A goal's now figure | 44 px | 44 px | 700 |
| Tile number | 27 px | 32 px | 700 |
| Chart card number | 24 px | 30 px | 700 |
| Card title (h2) | 17 px | 17 px | 600 |
| Goal title | 19 px | 19 px | 600 |
| Body and inputs | 16 px | 16 px | 400 |
| Secondary text | 15 px | 15 px | 400 |
| Labels, notes, tile labels | 13 px | 13 px | 500 |
| Form field labels and the radio group's legend | 14 px | 14 px | 500 |
| Tags, tile notes | 12 px | 12 px | 600 tags |
| Chart text on screen | 11 to 14 px, set per chart (Chart look) | 11 to 14 px | 400 axis, 600 values and labels |
| Tab label | 11 px under the icon | 14 px beside it | 600 |
| Landing title | 44 px | 64 px | 800 |
| Landing one-line | 24 px | 30 px | 600 |

- A unit beside a big number is about half its size, in `--text-2`.
- Labels are sentence case, never capitals.
- Proved by the owner's look at 390 px and 1,280 px.

#### Focus and tap targets

- `:focus-visible` draws a 2 px solid `--text` outline 2 px out. On an input the outline sits at 0 and the border turns `--text`.
- Two rings sit closer, as built: a day pill's link at 0, since the pills touch, and a fold-out's summary at 2 px in.
- No rule removes the focus outline without that replacement.
- **Every control is at least 44 by 44 px**, except a link inside a sentence.
- The day grid: the 7 columns run into the card's side padding by 12 px each side, with no gap between columns. Each pill link is 44 px tall and at least 44 px wide from a 360 px viewport up. The visible pill sits inset inside its link.
- Proved by T5 (a minimum size on each control class) and the live walk at 360 px.

#### Components and their states

New class names are named here. The kept ones are section H's.

| Component | Markup and classes | States | Proved by |
|---|---|---|---|
| Card | `div.card` bare on a plain card; `section.card` with `aria-labelledby` when titled; `card-head` holds the h2 and an `aside` | Default `--surface`. Empty: `is-empty`, transparent with a 1.5 px dashed `--line-strong` border, one line saying what to do, at most one action | T5, owner's look |
| Tile | `tiles` grid (3 across; `tiles four` is 2 by 2 on a phone, 4 across on a laptop). A tile is `tile` plus one of `tile-weight`, `tile-calories`, `tile-training`, `tile-maintenance`, `tile-pace`, `tile-run`, `tile-time`. Inside: hidden words naming it, `tile-value`, `tile-label`, then `tile-note` or a tag | With a sheet: a `button` with `popovertarget`, and a twin `a.tile-link` (see Today). Without a sheet: a `div`. Hover `--surface-hover`, pressed scale 0.97, focus ring. Empty: `is-empty`, dashed, words in the number's slot, no label, not a control | `test_page_rules.py`, owner's look |
| Chip | `ul.chips` of `li.chip` with `chip-weight`, `chip-lifts`, `chip-cardio` or `chip-food`; `chip-label` then the figure in `b` | Logged: `--raised` fill. Not yet: `is-missing`, dashed, words only. Not a control | Owner's look |
| Button | `button.primary` or `a.button.primary` (white pill, black text); `secondary` (`--raised` pill); `button.danger` (`--err` pill, black text); `button.link` and `a.action` (`--text-2`, 44 px tall); `wide` fills the card | Hover, pressed scale 0.98, focus ring. Disabled: `--raised`, `--text-3`, dashed border | T5, owner's look |
| Tag | `tag` plus `tag-you_said`, `tag-saved_food`, `tag-usda` (grey outline), `tag-claude_estimate` and `tag-estimate` (amber), `tag-sample` (sample colour, dashed), `tag-status` with `tag-good` (green, round dot), `tag-behind` (amber, square dot) or `tag-nodata` (dashed grey), `tag-closed` (grey) | Words always. `tag-claude_estimate` marks a food number the assistant estimated (the class keeps its stored name, section A). `tag-estimate` marks a worked-out estimate (cardio calories, maintenance). Both read "estimate" | T3, owner's look |
| Tab bar | `header.top` holds `a.brand`, `nav.tabs` and the sign-out form. A tab is `a.tab` plus `tab-today`, `tab-progress`, `tab-goals` or `tab-settings`. Signed out: `header.top-out` | Current: `aria-current="page"`, white pill, black text. Hover, pressed, focus | `test_page_rules.py` (the Navigation table) |
| Strip | `p.ok` (saved line, green dot); `div.ok.undo` with its Undo form; `p.notice.sample-banner`; `p.notice.goal-note`; `div.error` (a form error not tied to one field) | One strip per message, under the page head, above the first card | WEB |
| Fold-out | `details.add` with a `summary` that names the action ("Add weigh-in") | Closed by default. Sent open when its form has an error, or when the address carries its mark (`?add=food` for Food, see Today). Foods' save fold-out is also open when there are no saved foods. The plus mark turns 45 degrees when open | `test_page_rules.py` |
| Form field | `div.field` with the label above the input; `field-row` puts two side by side; `hint` under; `has-error` on the field | Focus: white ring. Error: 1.5 px `--err` border, the label in `--err`, `p.error` right under the input, tied by `aria-describedby`, the input `aria-invalid="true"`. What was typed stays | T5 |
| Segments | `nav.segments` of links | Current: `aria-current="true"`, white fill | WEB |
| Sheet | `div.sheet` with `popover`, `role="dialog"`, `aria-labelledby` its title; `sheet-head` (sticky) holds `grab`, then `sheet-bar` with `button.close` and `p.period`; then `giant`, `h2.noun`, `p.sentence`, `div.panel` with `panel-title` and a chart, a table, `p.sheet-link` | Closed, open. See Today and Motion | `test_page_rules.py`, owner's look |
| Table | Every table sits in `div.table-wrap`, which scrolls sideways. `th` and `td` with `num` are right-aligned, `nowrap`, tabular. `remark` cells in `--text-2`. `tr.group` starts a group with a stronger rule. `table.rows` stacks below 720 px: `td.lead` spans the row, every other cell shows its `data-label` above its value, a blank cell is not shown | Default, stacked | T5 |
| Big figure | `figure`, `big`, `unit`, `figure-label` | A figure, or words in its place | T3 |
| Day pill | `ol.days` of `li` holding `a.pill` with `is-lift` (filled), `is-cardio` (2.5 px outline), `is-none` (a short bar), and `is-today` added on the last (a `--today` ring). Hidden words in each | Pressed: the shape shrinks to 0.92 | `test_page_rules.py`, owner's look |
| Legend | `ul.legend` of keys (`key` with `is-lift`, `is-cardio`, `is-none`, `is-today`, `dot-key`; a line key is `key line` plus `plan`, `projection`, `goal` or `today`, since the stylesheet styles those four only as `.key.line.{name}`). Inside a goal card the legend is `p.legend` of `span` items: no `li` sits inside `li.goal` | | WEB (`goal_item` still reads a whole goal) |
| Address box | `code.link-box`, `ui-monospace`, on one line (`nowrap`), scrolls sideways inside itself, the whole text selected on a tap | Never wraps mid-word | WEB, live walk |
| Kept: `muted` | Secondary words | 15 px `--text-2` | Owner's look |
| Kept: `inline` | A one-button form inside a line | No margin, the button beside the words | Owner's look |
| Kept: `steps` | The landing's `ol.steps` | Each step a row; its number in a 32 px round `--raised` badge. No step is optional | T3, owner's look |
| Kept: `entry-line` | One row of a list | The words at the left, the figure or actions at the right, a 1 px `--line` rule under it | WEB |
| Kept: `attribution` | The estimates footer | 13 px `--text-3` on `--bg`, under the last card | WEB |
| Kept: `meal empty` | A meal with nothing logged | Its h3 in `--text-2`; "Nothing logged." in 13 px `--text-3` | WEB |
| Kept: `chart` | An `svg` chart | The width of its card up to its cap (Chart look), centred, its height from its viewBox | `tests/test_charts.py`, `tests/test_css.py` |
| Kept: `card trends` | The Trends card | A card holding the Trends table in its `table-wrap` | WEB |
| Kept: `goal` | `li.goal`, an open goal | A card, 24 px padding on a laptop | WEB |
| Kept: `facts` | A `dl` of facts | Each label in 13 px `--text-2` above its value in 16 px `--text` | WEB |
| Kept: `entries` | A `ul` of rows | No bullets; each row an `entry-line` | WEB |
| Kept: `totals` | The Food totals, `dl.facts.totals` | As `facts`; Total in 19 px, 700 | WEB |
| Kept: `goals` | `ul.goals` | No bullets; each goal a card, the card gap between them | WEB |
| Lift page tiles | `tile` plus `tile-max`, `tile-best` or `tile-sessions`, each with the `training.svg` icon | Not controls: a `div`, as a tile without a sheet | Owner's look |

- Visually hidden text uses `sr`. A value that must not break uses `nowrap`.
- **Every icon-bearing element carries its variant class.** The icon comes from `var(--icon)`, which only the variant sets. A `.tab`, `.chip`, `.tile` or `.chart-card .card-label` without its variant shows a solid square in the text's colour. So the lift page's Estimated max card label carries `tile-max`.
- `.small` is both a text class (13 px, `--text-3`) and the compact chart's `svg.chart.small`. SVG text inside a chart always takes a chart class (`axis`, `goal-label`), which sets its own size.
- Two page-state classes: `calm` on the `body` of a page after a write (Motion), and `roll`, with `aria-hidden="true"`, on the rolling digit columns (Motion, section J).
- The component classes above join section H's Kept by choice column.
- Proved by T5 for the stylesheet parts and by the owner's look for each state, at both widths.

### Icons

**Eleven hand-drawn icons, local SVG files, drawn through CSS masks in the text's own colour. An icon never carries meaning alone.**

| File in `app/static/icons/` | Where it shows |
|---|---|
| `today.svg` | The Today tab |
| `progress.svg` | The Progress tab, the best-time tile |
| `goals.svg` | The Goals tab |
| `settings.svg` | The Settings tab |
| `weight.svg` | The weight tile and chip, the weight chart card |
| `food.svg` | The calories tile, the food chip, the calories chart card |
| `training.svg` | The training tile, the lifts chip, the lift page's three tiles, the brand mark, the favicon |
| `maintenance.svg` | The maintenance tile |
| `run.svg` | The cardio chip, the pace and longest-run tiles, the pace chart card |
| `plus.svg` | A fold-out's summary |
| `close.svg` | A sheet's close button |

- Each file: `viewBox="0 0 24 24"`, round 2-unit strokes, no fill but one small solid detail where the shape needs it.
- Each file holds no `script`, no `style`, no `href`, no `data:` and no outside address.
- Each file keeps the SVG namespace attribute, `xmlns="http://www.w3.org/2000/svg"`. It names no resource. Step 4's icon test exempts it from its outside-address check.
- The eleven files are byte-identical to the approved static build.
- Drawn as `mask-image` (with the `-webkit-` form) over a `currentColor` fill, so the icon takes the colour of its words.
- Every icon sits on a pseudo-element beside a word. None is read aloud. None is a control on its own.
- Served from `/static` with `nosniff` and an SVG content type. `img-src 'self'` covers a mask image, so the policy does not change.

| Check | Proved by |
|---|---|
| The eleven files exist, each parses as SVG with that viewBox and none of the banned parts; the namespace attribute is exempt from the outside-address check | `test_page_rules.py` |
| Every icon `url()` in the stylesheet names one of them | T5 |
| `/static/icons/today.svg` answers 200 with `nosniff` | `test_page_rules.py` |
| The icons look right at 15 to 22 px | Owner's look |

### Chart look

**Every chart is inline SVG drawn by `app/charts.py` from service figures. Every chart has `role="img"`, a `<title>` and a `<desc>` that state its figures. Colours come from the stylesheet by class.**

#### Rules for every chart

- Variable sizes are SVG geometry attributes: `x`, `y`, `width`, `height`, `points`, `d`, `cx`, `cy`, `r`, `x1` to `y2`. No `style=`.
- Every line that draws in carries `pathLength="1"` (see Motion).
- No SMIL, no `<animate>`.
- The `<desc>` states the figures through `formatting.show`, `day` and `days`. Text from data is escaped.
- An axis label is a scale mark, written through `show`. It is never a figure the service did not return.
- On screen, chart text shows at 11 to 14 CSS px at every width from 360 px up. The sizes and caps below hold it.
- Counts in a desc go through `show(n, "count")` with their right word: "1 session", "28 weigh-ins".
- **Text never leaves the canvas.** Each left margin is the widest tick label's estimated width plus 6 plus 4 chart units. The unit stays on the top tick. The goal chart's right margin is its target label's width plus the 10-unit tick plus 6 plus 4. The plot is laid out after the labels.
- A label's width is estimated by character, on the wide side of the system fonts: a digit 0.58 em, punctuation and space 0.3, a capital 0.68, lower case 0.54; text at 600 and up 1.06 times wider (`charts.text_width`). The live walk checks other fonts (section I).
- **Labels that would meet give way.** The latest keeps its own and is held inside the canvas. When a chart's two end dates are one date, or would meet, only the last is written.
- **At most 5 labelled grid lines.** `_ticks` grows its step past the end of its table (times 2, 5 and 10) until 5 marks or fewer enclose the range. The pace chart takes at most 4, the weight sheet exactly 3. The plot spans the outer ticks, so no grid line sits outside it.
- **A point dated after today is drawn at today.** Its real date stays in its title and the desc. Points are sorted by drawn date, so no line doubles back. This holds for the lift, body weight, both weight charts and the goal's actual line. The goal's plan line is a line, not logged points, and is not moved.
- **0 kcal is food.** A day has food when its `kcal` is not null. A 0 kcal day gets its figure and no bar. Null is no food.
- The lift chart's desc names a projection only when the projection line is drawn. A goal date already passed draws none.

#### Text sizes and width caps

**Phone (under 960 px): 15 chart units, 30 on the compact pair. Each chart is capped in width, so 720 to 959 px stays at 14 px or less.** `charts.AXIS` and `charts.SMALL_AXIS` are the same 15 and 30, and every margin is worked out at that size. `tests/test_css.py` ties the two together.

| Chart | Phone units | Phone cap | Laptop units | Laptop cap |
|---|---:|---:|---:|---:|
| Body weight, `#bodyweight-chart` | 15 | 336 px | 13 | 386 px |
| Lift, `#progress-chart` | 15 | 336 px | 7.25 | 690 px |
| Goal path, `li.goal svg.chart` | 15 | 336 px | 13.4 | 373 px |
| Pace, `#chart-pace` | 15 | 336 px | 12.4 | 403 px |
| Today pair, `.glance svg.chart.small` | 30 | 149 px | 17 | 261 px |
| A sheet's chart, `.sheet svg.chart` | 15 | 336 px | 10 | none: a sheet is 560 px wide |

- The laptop units suit each chart's narrowest drawing, at 960 px. The cap stops its text passing 14 px at wider screens. So at 1,280 px a capped chart sits a little narrower than its column. The walk judges whether it sits well (section I).
- `tests/test_css.py::test_chart_text_shows_at_11_to_14_px_at_every_width` works each size out from the stylesheet's own numbers, at 360, 390, 720, 959, 960 and 1,280 px, and every 13 px from 360 to 1,600.
- The kept classes and element types stay: `grid`, `axis`, `series`, `series smoothed`, `dot`, `weigh-in`, `goal`, `goal-label`, `projection`; `line`, `polyline`, `circle`, `text`. `tests/test_charts.py` reads them.
- Each dot keeps its own child `<title>` with its day and value (`test_charts.py:94-95`). The kept charts keep their ids and titles: `progress-chart` and "Estimated max over time: {lift}" (`test_charts.py:35`), `bodyweight-chart` and "Body weight over time" (`:180`).

#### The marks

| Class | Element | Look |
|---|---|---|
| `grid` | `line` | 1 px `--line` |
| `axis` | `text` | `--text-3`, at the chart's size (above); `axis value` in `--text-2`, 600; `axis is-today` in `--text`, 700. `end` and `mid` set the anchor |
| `series` | `polyline` | 2.5 px `--text`, round joins and caps, no fill |
| `series smoothed` | `polyline` | 3 px |
| `area` | `path` | `--text` at 10% fill, no stroke. Decorative: the line above it carries the data |
| `dot` | `circle` | `--text` fill |
| `dot weigh-in` | `circle` | `--surface` fill, 1.5 px `--text-2` ring |
| `dot is-latest`, `dot end-dot` | `circle` | `--text` fill, a 3 px ring of the card's colour |
| `dot proj-end` | `circle` | `--surface` fill, 2 px `--text-2` ring |
| `goal` | `line` | 1.5 px `--text-2`, dotted |
| `goal-label` | `text` | `--text`, at the chart's size, 600 |
| `plan` | `line` | 2 px `--line-strong`, dashed 8 on 6 off |
| `projection` | `line` | 2 px `--text-2`, dotted, round caps |
| `today-line` | `line` | 1.5 px `--today` |
| `today-label` | `text` | `--today`, 600 |
| `target-tick` | `line` | 2.5 px `--text`, round caps |
| `bar` | `rect` | `--text-2`, rounded top; `bar is-today` in `--text` |

- A compact chart in a chart card adds `small` to the `svg`, which thickens its lines and enlarges its axis text.
- The `projection` line keeps its `stroke-dasharray` attribute, which `test_charts.py:40` reads.

#### The charts

| Chart | Page | What it draws | Empty state |
|---|---|---|---|
| Weight, 4 weeks | Today chart card | The smoothed line over the last 28 days, the area under it, the end dot. No axis | Keyed on the weigh-ins in the 28 days: none, "No weigh-ins in the last 28 days."; one, "One weigh-in in the last 28 days. The chart starts from the second one." |
| Eaten a day, 7 days | Today chart card | One bar per day, today's bright, a one-letter day under each (`weekday_letter`). A dotted `goal` line at the calorie target when one is set. A day with no food has no bar | No food in the 7 days: "No food logged in the last 7 days." |
| Smoothed weight | Weight sheet | The same line and area, three labelled grid lines, the first and last date | As above |
| Each day | Calories sheet | Seven bars, each with its kcal above it and its weekday under it (`weekday`) | As above |
| Body weight | Progress | A hollow dot per weigh-in of the last 28 days, the smoothed line, labelled grid lines in whole kg, the first and last date, the goal line with "Goal {target} kg". Drawn by the kept `bodyweight_svg` from `progress.bodyweight_chart` with `since` 28 days back (section D) | Under 2 weigh-ins in the 28 days: the card's words, no chart |
| Goal path | Goals; the landing picture | The dashed plan line from start to target, the actual line with a dot per point, now ringed at today, the dotted projection from now to the target at the projected date, the today line with "Today", a target tick with its value, labelled grid lines. The first date label is the start date and the last the target date; when the target is on or before the start, only the target date | One point: the plan line, one ringed dot, the today line. No projection without a projected date |
| Pace | An activity page | A dot per session with its pace above it, joined by a line, the latest ringed, at most 4 grid lines in m:ss, a date under each dot. Sessions sit evenly spaced, oldest at the left. Dates and paces that would meet give way, the latest keeping its own. Lower is faster and the card says so | Under 2 sessions with a distance: no chart, the card says why |
| Estimated max | A lift page | The kept lift chart, restyled: the series, a dot per session, the goal line, the projection | No session of 1 to 10 reps: the page's words |

- The goal path's projection runs from (today, now) to (projected date, target). With a projection but no projected date it runs to (target date, projected value). With no projection there is no line. A projected date far past the target is drawn at half the plan's length beyond it, and a wild projected value inside the grid. The desc keeps the real figures.
- The plan line is drawn from the read's `path.plan` points. The actual line from `path.actual`.
- **The goal chart's now is the goal's `latest`, ringed at today.** `path.actual` can end before today. A point of it at today takes `latest`, so the line meets the ring.
- **The goal chart's desc, in order:**
  1. The start: "Started {start} on {day}.", or "Started {day}." with no start value.
  2. The goal's `plan_line`, word for word. "Nothing logged yet." when it is null.
  3. "Target {target} by {day}."
  4. "Needed {pace a week}." and "So far {pace a week}.", each when there is one.
  5. "At this pace it is reached on {day}.", when there is a projected date.
- The desc never makes its own comparison with the plan line, so the close-call rules of section D hold there through `plan_line`. Its other figures go through `show`, never widened.

| Check | Proved by |
|---|---|
| Each chart function returns one well-formed SVG with `role="img"`, a title and a desc, text escaped, no `style` attribute | `tests/test_charts.py` |
| Each new chart's empty input returns nothing, and the page shows its words | `tests/test_charts.py`, WEB |
| Every `<svg` on every page has `role`, `title` and `desc` | `test_page_rules.py` |
| The desc of the Progress chart and the goal charts hold exactly `show` of the read's figures; a goal chart's desc holds its `plan_line` word for word | T7, `tests/test_charts.py` |
| Every label inside its viewBox and overlapping no other, the widest inputs among them; at most 5 grid lines; a point after today at today; 0 kcal as food; every number in a chart from the formatter | `tests/test_charts.py` |
| Each chart drawn from the real reads on the sample account | `tests/test_charts.py`, `tests/test_cardio.py` |
| The look of each mark | Owner's look |

### Today

**Today answers "what have I done today" and "how am I doing", in one template. The same structure serves a full day, an empty day and a new account. A past day is the same template without the "now" figures.**

#### Order on a phone, which is the markup order

1. Page head: h1 "Today" with the day in full under it, from `formatting.day_heading` ("Today, Tuesday 10 November" when read aloud). The day links: "Previous day", and "Next day" on a past day.
2. The sample banner, when sample rows exist.
3. One strip: the saved line or the Undo strip (see the strips table).
4. The Today card.
5. The glance block, on today's page only: three tiles, their three sheets, the Last 28 days card, the chart pair (Weight, Eaten a day).
6. The Food card.
7. The Lifts card.
8. The Cardio card.
9. The Weigh-in card.
10. The Measurements card.
11. The attribution footer, when a cardio estimate shows.

#### On a laptop

- Two columns that each stack. The left is 5 of 12 columns: the Today card, then `div.col-main` (Food). The right is 7 of 12: `div.glance`, then `div.col-side` (Lifts, Cardio, Weigh-in, Measurements).
- Both columns keep the markup order. A block's top edge is never above the top edge of a block before it in the markup.
- Uneven column ends are accepted, a new account included.
- The page head, the banner and the strip span both columns.
- **A past day has no glance block**, so the Today card spans both columns. Food sits on the left under it, and `div.col-side` on the right, both starting on the same line. The server marks it `main.page.today.past`, also for a day after today: the layout keys on that class, never on `:has()`.

#### The Today card

| State | When | Top line | Main | Chips | White control |
|---|---|---|---|---|---|
| New account | Today's page, and `sample_state` says empty (`web.py:650`) | "Today" | h2 "Nothing logged yet", then the sample paragraph in today's words (`day.html:14-15`) | No food yet; No weigh-in yet; No lifts yet. All dashed | "Add sample data", the existing POST form to `/settings/sample` |
| A day with food | Any day page | "Eaten today", or "Eaten" on another day | The day's kcal, big, with its unit | Weigh-in; Lifts; Cardio | "Add food", a link to the same day page with `?add=food#add-food` |
| A day with no food | Any day page, not a new account | As above | The words "No food yet" in the number's slot | As above | As above |

- Under the number: "No calorie target set", or "{target} kcal target, {left}" where {left} follows the `kcal_left` rule.
- **Chips.** Weigh-in: "Weigh-in {kg} kg". Lifts: "Lifts logged". Cardio: the day's first cardio entry, "{activity} {time}". Missing: "No weigh-in yet", "No lifts yet", "No cardio yet"; on another day without "yet".
- The chips show entries. They add nothing up.
- The card carries no sample tag: the tag sits on the entries below, so the page's sample-tag count is unchanged (`test_web_account.py:157`).

#### Tiles and sheets

| Tile | Number | Label | Note | Sheet |
|---|---|---|---|---|
| Weight | The smoothed weight | "kg trend" | The rate, "{rate} kg a week"; "No weekly rate yet" without one | Weight trend |
| Calories | The 7-day average | "kcal eaten a day" | "last 7 days" | Eaten a day, on average |
| Training | Lift sessions in the last 7 days | "lift sessions" | "last 7 days" | Lift sessions |

- With no weigh-in at all, the Weight tile is empty: "No weigh-ins yet", dashed, no sheet. With weigh-ins but none in 28 days it still shows the smoothed weight and opens its sheet; the sheet's chart says why it is empty.
- With no food in 28 days, the Calories tile is empty: "No food in the last 28 days". With food in 28 days but none in 7, it is empty too: "No food in the last 7 days", and its sheet does not open: the Trends rows carry the 28-day figures.
- Training always shows a number: 0 is a true zero.
- **Each tile that opens a sheet is two controls in the markup.** A `button.tile` with `popovertarget`, and a twin `a.tile.tile-link` to the matching part of Progress. The stylesheet shows the button and hides the link under `@supports selector(:popover-open)`, and the reverse otherwise. No script.
- The twins carry the same words. The hidden one is `display: none`, so it is never read or tabbed to. T3 and T7 read the page text and accept the figure twice. A count test never counts a tile figure. No tile holds a sample tag.
- Twin targets: Weight to `/progress#body-weight`; Calories, Training and Maintenance to `/progress#trends`. On Progress itself the twins are `#body-weight` and `#trends`.

| Sheet | Period | Number | Title | Sentence | Panel | Table |
|---|---|---|---|---|---|---|
| Weight | "Last 4 weeks" | The smoothed weight | "Weight trend" | "The trend moved {rate} kg a week from {from} to {to}. The {basis} is {now} kg, from {n} weigh-ins in 28 days." | "Smoothed weight" chart | Smoothed trend; {basis}; Rate, remark {from} to {to}; Weigh-ins, remark "In the last 28 days" |
| Calories | "Last 7 days" | The 7-day average | "Eaten a day, on average" | "Over the last 28 days it is {avg_28d} kcal a day, and {share} of it is estimates." Then the target, or "No calorie target is set." | "Each day" chart | Eaten a day, remark "Last 7 days, {n} logged"; Eaten a day, remark "Last 28 days, {n} logged"; From estimates, remark "Last 28 days"; Calorie target, value blank and remark "Not set. Set one on Settings." when unset |
| Training | "Last 7 days" | Lift sessions | "Lift sessions" | "{per_week_4w} a week over the last 4 weeks. Cardio came to {min} min in the last 7 days, and {min_4w} min a week over 4 weeks." Then the targets, or "No session or cardio target is set." | None | Week ending; Lift sessions; Cardio (min): four rows, oldest first |
| Maintenance (Progress only) | "{from} to {to}" | The maintenance figure with an estimate tag | "Maintenance, a day" | "About {kcal} kcal a day, worked out from {days} logged days. Over that window intake averaged {intake} kcal a day and the 7-day average weight moved {change} kg." Then "An estimate from your intake and weight change. Your calorie target stays yours to set." | None | None |

- {basis} is "7-day average" or "latest weigh-in", today's rule (`web.py:1124-1133`).
- The weight rate's sentence is left out without a rate, and the sheet says why in the read's words (`web.py:1136-1139`).
- A Today sheet ends with a secondary "Open Progress" link. A Progress sheet has none.
- **The sheet's behaviour.** The close button comes first, carries `popovertargetaction="hide"` and `autofocus`, and is 44 by 44. Esc and a tap outside close it. Focus goes back to the tile.
- In a long sheet the head, with the close button, stays in view while the sheet scrolls.
- Phone: the sheet rises from the bottom, full width up to 560 px, at most 88% of the screen tall, with a grab handle. Laptop: centred, at most 560 px wide and 86% tall, every corner rounded, no grab handle.
- A sheet is not modal and holds no form. It repeats figures that Progress shows.

#### The Last 28 days card

- Head: h2 "Last 28 days", and the aside "{n} lift sessions" (one: "1 lift session").
- A row of seven weekday labels (`weekday_letter`), hidden from assistive technology, starting at the weekday of the first day shown.
- `ol.days`: 28 links, oldest first, each to its day page.
- A pill is `is-lift` on a day with a lift session; `is-cardio` on a day with cardio and no lift session; `is-none` otherwise. The last adds `is-today`.
- Each link's hidden words: "{day}: lift session", "{day}: cardio only" or "{day}: nothing trained", with ", today" after the date on the last.
- A legend of four keys under it.
- A new account shows 28 `is-none` pills and "0 lift sessions".

#### The chart pair

- Two chart cards side by side at both widths: "Weight", sub "Trend, 4 weeks", the smoothed weight; "Eaten a day", sub "Average, 7 days", the 7-day average. Each with its compact chart.
- Empty: the card turns `is-empty` and shows the chart's empty words.

#### The entry cards

| Card | An entry shows | Its fold-out | Empty line |
|---|---|---|---|
| Food | By meal, in the order Breakfast, Lunch, Dinner, Snack. `div.meal` with an h3; `div.meal empty` with "Nothing logged." in small grey. Each entry: the name on line one (the saved food's name, else the description); "{kcal} kcal, {serving words}" on line two; a description that differs from the saved food's name on its own grey line; the source tag and the sample tag in their own slot, the sample mark shown once (section F, held small fixes); Confirm on an estimate, Edit, Delete | "Add food": Food; Calories (kcal); Meal, whose empty choice reads "By the clock"; "Save food". Closed by default. Sent open when the address carries `?add=food`, the mark the Today card's white button sends, and after its error. That mark also makes the page calm | "No food logged on this day. Tell your assistant what you ate, or add it below." |
| Lifts | Each session: "Lift session" with its sample tag, Edit and Delete. Its sets in a table: Exercise, Set, Weight (kg), Reps. Each new best on its own line. Its notes on their own line, labelled "Notes" | None. No hand form for lifts in this stage | "No lifts logged on this day. Tell your assistant what you lifted and it shows here." With sessions, a small line under them: "Lifts are logged through your assistant." |
| Cardio | The activity on line one. Then labelled figures: Time (m:ss), Distance (km), Pace (per km), Calories (kcal) with the estimate tag, Machine (kcal). A figure the entry lacks is left out, except the calorie estimate, which says "No estimate". Notes on their own line, labelled "Notes". Edit, Delete | "Add cardio": Activity; Time (min); Distance (km), optional; "Save cardio" | "No cardio logged on this day. Tell your assistant what you did, or add it below." |
| Weigh-in | The weight, large, with its sample tag, Edit, Delete | "Add weigh-in" or "Replace weigh-in": Weight (kg); the button says the same | "No weigh-in on this day." |
| Measurements | The kind's name from `measurements.kind_word` ("Resting heart rate"), then the value and unit. Edit, Delete | "Add measurement": Kind, each choice named by `measurements.kind_word` with its unit; Value; "Save measurement" | "No measurements on this day." |

- **The web reads every measurement name from `measurements.kind_word`** (section D): the Measurements card, its fold-out, the edit page and the Goals form. `web.MEASUREMENT_LABELS` goes, and no template holds its own copy of a kind's word.

- **The Food totals**, a `dl.facts.totals` under the meals: Total (bold, larger); From estimates, when any; Share from estimates; Target and Left, when a target is set; Net (food minus cardio), when add-back is on. No "of which", no brackets around a figure.
- An entry with no cardio estimate shows "No estimate", and the card keeps the footnote step 2 shipped: "No calorie estimate on a row that says No estimate: there was no weigh-in when it was logged. Log a weigh-in and new cardio gets one."
- The fold-out rule: closed by default; sent open when its form has an error, or when the address carries the fold-out's mark (`?add=food`). Any control that points at a closed fold-out is a link carrying the fold-out's mark. No script.
- An error sits under its field, in the page's own words, tied by `aria-describedby`.

#### The strips after a write

| Address carries | Strip | Where it comes from |
|---|---|---|
| `deleted={kind}:{id}` | Undo strip: "{Kind} deleted." and an Undo button | Today (`web.py:926`, `day.html:25-33`), with the subject named |
| `saved={kind}` | "{Kind} saved." | New: the day writes (`web.py:788`, `:851`) |
| `saved=confirmed` | "Estimate confirmed." | New: Confirm (`web.py:911`) |
| `restored={kind}` | "{Kind} restored." | New: Undo (`web.py:944`) |
| `saved=sample` | "Sample data added." | New: Add sample data, through `/today` (`web.py:1324`) |
| `deleted=sample` | "Sample data removed." with no Undo (D15) | New: Remove sample data, through `/today` (`web.py:1338`) |

- {Kind} is the entry kind's one spelling from the tag table, `web.ENTRY_WORDS` as step 2 shipped it: Weigh-in, Measurement, Lift session, Food entry, Cardio session.
- `/today` passes `saved` and `deleted` on to the day address (`web.py:598` drops them today).
- These marks also switch on the calm page (see Motion).
- As built in step 4: a form low on a page (the day page's four fold-outs, Set a goal, Save a food) posts to its own anchor, so a refused post comes back at the form, and each redirect that answers it ends in `#top` (`body` carries `id="top"`), so the strip is in view. Settings' three forms each post to their own card's anchor, and each redirect that answers one names that anchor, since the saved line sits in the card.

#### A past day

- The same template. h1 is the day in full, from `formatting.day_heading` ("Monday 9 November", "Yesterday, Monday 9 November", the year when it is not this year).
- Both day links. The Today card reads "Eaten", with its chips and "Add food".
- No glance block: no tiles, sheets, grid or chart pair. They are "now" figures and would mislead on a past day.
- A day after today, left there by a time-zone change, is treated as a past day.

#### The edit pages

- Page head: h1 "Edit {kind}" in sentence case, {kind} from `web.ENTRY_WORDS` as step 2 shipped it, and a link back to the day.
- One card, at most 640 px wide and centred on a laptop.
- Fields stacked, labels above, each label naming its unit: Weight (kg); Value, under the measurement's name and unit; Food, Calories (kcal), Meal; Activity, Time (min), Distance (km), Incline (%), Speed (km/h), Average heart rate (bpm), Machine's calories (kcal), Notes; Notes; Date.
- Values in the inputs come from `formatting.field`.
- A food entry's card opens with its source tag and the line that changing the kcal makes it a number you said (`edit.html:18-20`).
- A cardio entry's card ends with the estimate now, in grey, and that it is worked out again on save.
- A lift session's page adds a read-only Sets card: the sets table and "Sets are changed through your assistant for now: tell it what to fix."
- "Save" (white) and "Cancel" (a link to the day).
- The page shows the attribution footer when a cardio estimate shows (`web.py:858`).

| Check | Proved by |
|---|---|
| The structure and order above, on the three accounts | WEB, T3 |
| The Today card's three states and its white control | WEB |
| Tile twins: every `popovertarget` names a popover on the page; every tile button has a twin link with the same words | `test_page_rules.py` |
| The pill classes on the fixed day: lifts Monday, Wednesday and Friday; cardio Tuesday, Thursday and Saturday; none on Sunday | WEB, against `trends.get_days` |
| The Food fold-out open only with `?add=food`; every other fold-out closed; a fold-out open after its error | `test_page_rules.py`, WEB |
| A past day has no tiles, sheets or grid | WEB |
| The sample-tag count on a marked day stays 7 | `test_web_account.py:157` |
| The look at both widths, a sheet opened and closed by touch and by keyboard | Owner's look, live walk |

### Progress

**Progress is "how am I doing" in full: four tiles, the body weight chart, the Trends table, cardio by activity, lifts by lift. A lift and a cardio activity each have their own page.**

#### Order on a phone, which is the markup order

1. Page head: h1 "Progress".
2. The sample banner.
3. Four tiles in 2 by 2: Weight, Calories, Training, Maintenance. Their four sheets.
4. The Body weight card, `id="body-weight"`.
5. The Trends card, `div.card.trends`, `id="trends"`.
6. The Cardio card.
7. The Lifts card.

- **Cardio comes before Lifts in the markup, at both widths.** On a laptop Cardio sits under Body weight and beside Trends, above Lifts. One order serves both.

#### On a laptop

- Tiles 4 across.
- Body weight in columns 1 to 7. Trends in 8 to 12, spanning two rows.
- Cardio in columns 1 to 7, under Body weight.
- Lifts across all 12 columns.
- No dense packing. The grid's normal placement gives this order.

#### Tiles

- Weight, Calories and Training as on Today.
- Maintenance: the maintenance figure, label "kcal to maintain", the estimate tag in its own slot under the label. Opens the Maintenance sheet.
- Without an estimate the Maintenance tile is empty, "Not enough logged yet", dashed, no sheet. The Trends row gives the reason.

#### The Body weight card

- Head: h2 "Body weight", aside "4 weeks".
- The chart over the last 28 days, from `progress.bodyweight_chart` with `since` (Chart look).
- Four callouts beside it on a laptop, under it on a phone in 2 by 2, in this order: High, Latest, Trend, Low. Each: the label, the kg, and the date or "Smoothed" under it. Trend is boxed in a white outline.
- High, Latest and Low come from E2 `weight`. Trend comes from `get_trends` `bodyweight.smoothed`.
- A legend: Weigh-in, Smoothed trend, Goal with its target when one is open.
- A small line: "Dots are weigh-ins. The line is the smoothed trend, which evens out day-to-day swings."
- No weigh-ins at all: the card is empty, "No weigh-ins yet. Add one on Today."
- None in the 28 days: "No weigh-ins in the last 28 days.", and the Trend callout alone.
- One in the 28 days: the callouts, and "The chart starts from the second weigh-in." in place of the chart.

#### The Trends card

A table: Quantity, Value, Remark (D20). One row per quantity, in groups.

| Group | Quantity | Value | Remark |
|---|---|---|---|
| Weight | Smoothed trend | kg | |
| | 7-day average, or Latest weigh-in | kg | |
| | Weight rate | kg a week | {from} to {to}; or the reason there is none, value blank |
| | Weigh-ins | count | Last 28 days |
| Food | Eaten a day | kcal | Last 7 days, {n} logged |
| | Eaten a day | kcal | Last 28 days, {n} logged |
| | From estimates | % | Last 28 days |
| | Calorie target | kcal | "Not set" with the value blank, when unset |
| Training | Lift sessions | count | Last 7 days; and "Target {n}" when set |
| | Lift sessions a week | count, up to 1 dp | 4-week average |
| | Cardio | min | Last 7 days; and the target when set |
| | Cardio a week | min | 4-week average |
| Maintenance | Maintenance | kcal, nearest 10 | The estimate tag, then "{days} logged days, {from} to {to}"; or the reason, value blank |

- As built in step 4: Lift sessions a week reads "{n} a week" in every state, so an average that is not whole keeps its unit. A target set reads in the remark as "Last 7 days, target {n}".
- A value cell holds a number and its unit, or nothing. Words go in the remark.
- No food in 28 days: the food rows show a blank value and the remark "No food logged in the last 28 days."

#### The Cardio card

- A row table, one row per activity logged, by name: Activity (a link to its page; **not built in step 4:** the name is plain text, since an activity's page is not built); Sessions; Time (min); Fastest pace (per km); Last (a day link).
- An activity with no distance logged leaves its pace cell blank. The cell holds no words.
- Under the table: "Sessions and time cover the last 28 days. Fastest pace is your best ever. A blank pace means no distance is logged for that activity."
- None logged: "No cardio logged yet. Tell your assistant what you did, or add it on Today."

#### The Lifts card

- A row table, one row per lift logged, by name: Lift (a link to its page); Estimated max; Best set; Trend; Last trained (a day link).
- Best set: "{weight} kg × {reps}".
- **A lift with no added weight** (no live set above 0 kg, so it is in `reps_only`): Estimated max reads "Needs added weight"; Best set "{reps} reps", from `reps_only`; Trend blank. It never shows "0 kg". On the fixed day, Pull-Up reads "Needs added weight" and "6 reps".
- A weighted lift with no set of 1 to 10 reps: Estimated max reads "Needs a weighted set of 1 to 10 reps"; Best set and Trend blank. (As built in step 4: "Needs a set of 1 to 10 reps" was untrue for a person who did such a set at 0 kg.)
- These two states replace step 2's "No estimated max yet" and "No best set yet": a deliberate step 4 change, since `reps_only` (step 3) tells the two reasons apart.
- Trend: the slope, "{rate} kg a week"; blank when there is none.
- Under the table: "Estimated max is worked out from your sets, not a tested max. Trend is its change a week."
- None logged: "No lifts logged yet. Tell your assistant what you lifted and it shows here."

#### A lift's page, `/progress/{lift}`

- Page head: h1 the lift's name, a back link "Progress".
- The sample banner.
- Three tiles, not controls: Estimated max now (`tile-max`, or the words above); Best set, with its date (`tile-best`); Sessions (`tile-sessions`).
- For a lift with no added weight, the Estimated max tile reads "Needs added weight" and the Best set tile "{reps} reps", with its day, from `lifts.history`'s `reps_only`, read the same way as on Progress. Never "0 kg". `get_progress(lift)` does not carry it.
- A chart card, "Estimated max", with the lift chart.
- A Bests card: newest first, each "{day}: {words} {value}, up from {previous}", the figures through `show` with their unit, the day a link. A best with no previous shows its value only. This replaces step 2's "(was {previous})": a deliberate step 4 change, so the page uses a new best's own words and no brackets. {words} come from `lifts.BEST_LABELS` ("estimated max", "heaviest weight"). The two figures take `estimated_max` for an estimated max and `weight_kg` for a heaviest weight, and both go through `show(value, quantity, widened=True)` when they print the same.
- A Sessions card: newest first, each "{day}: estimated max {value} kg".
- Laptop: tiles across; the chart in 8 columns beside Bests in 4; Sessions across.
- Nothing logged: one card with today's line (`progress_lift.html`), saying "your assistant": "No sets of {lift} logged yet. Tell your assistant what you lifted and it shows here."
- As built in step 4: a weighted lift with no set of 1 to 10 reps reads "Needs a weighted set of 1 to 10 reps" on the Estimated max tile and "No weighted set of 1 to 10 reps yet" on the Best set tile. The chart card's sub line is "Worked out from your sets, not a tested max". With no chart it says "The chart starts from a set with added weight." or "The chart starts from a weighted set of 1 to 10 reps." The Sessions tile's note is "last trained {day}". An empty Bests card says "No new bests beyond the first session yet.", an empty Sessions card "No session has an estimated max yet."

#### An activity's page, `/progress/cardio/{activity}`

**Not built in step 4:** the page was cut from this merge, so the Cardio card's activity names are plain text. The section stays as the plan.

1. Page head: h1 the activity's name, a back link "Progress".
2. The sample banner.
3. Three tiles, not controls:

| Tile | Number | Label | Note |
|---|---|---|---|
| Fastest pace | m:ss | "fastest per km" | Its day |
| Longest | km | "km, longest" | Its day |
| Best time | m:ss | "best {distance} km", the longest of 1, 5 and 10 km reached | Its day |

4. The Pace chart card: "Pace", sub "Per km, each session. Lower is faster."
5. The Sessions card: a row table, newest first: Date (a day link); Time; Distance (km); Pace (per km); Calories (kcal); Machine (kcal); Tags; Notes. A column blank in every row is not shown. Tags holds the estimate tag and the sample tag.

- Laptop: tiles in columns 1 to 7, the Pace card in 8 to 12 on the same row, Sessions across all 12. The markup order is tiles, then Pace.
- A tile with no figure is empty, with words: "No distance logged yet".
- No sessions: one empty card with the read's note ("No Outdoor Run logged yet."), no tiles and no chart.
- It shows estimates, so it carries the attribution footer.

| Check | Proved by |
|---|---|
| Order, tiles, the Trends rows with remarks in their own column | WEB, T3 (furniture) |
| Each figure equals `show` of its read's field | T7 |
| The Pull-Up row on the fixed day: "Needs added weight", "6 reps", no trend, no "0 kg"; the same on Pull-Up's own page | WEB, against `reps_only` on the fixed day |
| The activity page: bests, sessions, the attribution | WEB |
| The look at both widths | Owner's look |

### Goals

**Every open goal is a card with its path chart and two statements named apart. Then closed goals. Then one form that shows only the fields its kind needs.**

#### Order on a phone, which is the markup order

1. Page head: h1 "Goals".
2. The sample banner.
3. The saved line, in the words step 2 shipped ("Goal set.", "Goal marked as hit.", "Goal dropped.", "Goal reopened."), then each goal note (`p.notice.goal-note`, today's words, `web.GOAL_NOTES`).
4. The open goals: `ul.goals` of `li.goal id="goal-{id}"`.
5. The Closed goals card, when any.
6. The Set a goal card, `aside.card.set-goal`, `id="set-goal"`.

- Laptop: items 2 to 5 sit in `div.goals-main`, columns 1 to 8; Set a goal in 9 to 12, sticky under the top bar. Both start on the same row.

#### A goal card, in markup order

| Part | What it shows |
|---|---|
| Head | h2 `<strong>{label}</strong>`; "by {target date}" under it; the sample tag |
| Now | The now figure, 44 px, in the goal's unit; then grey words for what it is, from the read's `now_basis` (table below). Nothing logged: the words "Nothing logged yet" |
| Standing | Two boxes side by side. **Against the plan line**: a heading from `vs_plan` and `line_ended` (table below); under it the goal's `plan_line` as written; under that "{n} days ahead" or "{n} days behind" ("1 day"), only when `days_ahead` is neither 0 nor null. With `days_ahead` 0 or null the box shows the gap alone, in the sentence, and no days figure. Nothing logged: "Nothing logged yet" and no heading. **By projection**: the tag Reached, On track, Behind or Not enough data, shown once; under it what the tag means |
| Chart | The goal path chart, then a `p.legend`: Plan line, Actual, Projection, Today |
| Facts | Started: {start} with its day under it. Plan line today: {plan}. Needed: {pace a week}. So far: {pace a week}. At this pace: {day} with "reached" under it |
| Actions | Hit (secondary button), Drop (link button), each its own status form |

| Kind | `now_basis` | Words |
|---|---|---|
| Body weight | `average_7d` | "now, the 7-day average" |
| Body weight | `latest_weigh_in` | "now, the latest weigh-in" |
| Lift | `best_28d` | "now, the best estimated max in 28 days" |
| Lift | `latest` | "now, the latest session's estimated max" |
| Cardio time | `best_28d` | "now, the best {distance} km time in 28 days" |
| Cardio time | `latest` | "now, the latest {distance} km time" |
| Measurement | `latest` | "now, the latest {measurement}", the measurement's word from `measurements.kind_word`, lowered |

**The heading of Against the plan line.**

| `vs_plan` | While the line runs (`line_ended` false) | Once the line has ended or has no length (`line_ended` true) |
|---|---|---|
| ahead | "Ahead of the plan line today" | "Ahead of the target" |
| on the line | "On the plan line today" | "At the target" |
| behind | "Behind the plan line today" | "Behind the target" |

- **The comparison sentence is the goal's `plan_line`, as written.** The page never builds a comparison from its own arithmetic. So the close-call rules of section D hold on the page through `plan_line`.
- **The heading words come from `vs_plan`, and `line_ended` picks "the plan line today" or "the target"**, as `plan_line` does. The page never works out whether the line has ended.
- **The two statements can disagree, and both show.** The sample body weight goal on the fixed day shows them disagreeing (section E1).
- What the tag means: On track, "At this pace the target is reached by the date." Behind, "At this pace the target is not reached by the date." Reached, "The target is reached." Not enough data: the goal's own note ("A projection needs 4 points in the last 6 weeks.").
- As built in step 4: a goal whose tag is Behind shows one line under the facts, "Ask your assistant how to reach this target by its date." It names no label and no figure.
- **Rule 3.** On the plan line, the card shows the now figure, the heading and `plan_line`, and leaves out the Plan line today row.
- **Rule 2.** The now figure and Plan line today print exactly the figures `plan_line` prints: through `show` with `page=True`, widened when `formatting.widen([latest, should_be_today], quantity)` is true, from `goal_charts`' unrounded figures. T7 holds that they match `plan_line`.
- On the plan line, no days line shows.
- A target date that has passed: the plan figure is the target, and no days line shows (`days_ahead` is null).
- Words for a missing fact: Needed "Not worked out yet"; So far "Not enough data yet"; At this pace "No date yet"; reached, "Reached".
- No `li` sits inside `li.goal`, so `goal_item()` still reads a whole goal.

#### On a laptop, inside a goal card

- Head, Now and Standing across the card.
- The chart on the left (about 57%), the facts on the right; the actions under the chart.

#### Closed goals

- `ul.entries` of `li.entry-line id="goal-{id}"`: the label; "Closed {day}" in grey, the day in the person's own zone through `formatting.day_utc`; the tag Hit or Dropped (`tag-closed`); Reopen (link button).

#### Set a goal

- h2 "Set a goal".
- `nav.segments`, four links: Body weight, Lift, Cardio time, Measurement. Each goes to `/goals?kind={kind}#set-goal`. The current one carries `aria-current="true"`. Body weight is current when none is named.
- The form carries the kind as a hidden field and shows only that kind's fields:

| Kind | Fields, in order |
|---|---|
| Body weight | Target weight (kg); By (a date); Notes. A grey line: "The goal starts from your 7-day average today." (As built in step 4: the line is left out when "Where you are now" shows, since the goal then starts from the number given.) |
| Lift | Lift (the lift list); Target estimated max (kg); By; Notes |
| Cardio time | Activity (the cardio list); Distance (km); Target time (min), with the hint "Minutes, such as 27.5 for 27:30"; By; Notes. (As built in step 4: "Where you are now (min)" carries the same hint when it shows.) |
| Measurement | Measurement (waist, hips, chest, arm, thigh, resting heart rate, each with its unit); Target; By; Notes |

- The empty choices keep step 2's words, "No lift or activity" and "No measurement", until the one-kind form lands. Then each list's empty choice names its own kind: "No lift", "No activity", "No measurement". A deliberate step 4 change.
- "Where you are now" shows only when the service refused for want of a start, or the person typed one. It then shows with its error under it.
- An error sits under its field, in the page's own words, never the service's `message` or `fix` (section C). A refusal tied to no field sits in a `div.error` at the top of the form, in the page's own words too.
- **The page places a service error by its `error` code or its `field`, never by matching the words of its `message`.** Step 3's kind words changed "sleep hours cannot be a goal" to "Sleep cannot be a goal", and today's `web.goal_error_field` text match no longer finds it.
- `tests/test_web_goals.py` uses a real service message there, from `set_goal`, never a hand-made one.
- This lands in step 4, in lane B. The tests that hold today's markup (`test_web_goals.py:255-264`) move with it. Until step 4, T3 does not check it.
- After a refusal the page comes back with the posted kind current and what was typed kept.
- "Set goal" (white).

#### Empty

- No open goals: one dashed card, "No open goals. Set one, or tell your assistant what you are aiming for." The form follows it. (As built in step 4: "below" was untrue on a laptop, where the form sits to the card's right.)

| Check | Proved by |
|---|---|
| The two namings apart; rule 1, rule 2 and rule 3 states; the heading by `vs_plan` and `line_ended`; with `days_ahead` 0 or null, no days figure | `tests/test_web_goals.py` (E1's tests) |
| The comparison sentence is `plan_line` as written, on the sample and edge accounts | `tests/test_web_goals.py`, T7 |
| A service error placed by its code or field, with a real `set_goal` message; "Sleep cannot be a goal" lands beside the measurement field | `tests/test_web_goals.py` |
| The kind segments show only their fields; a refusal comes back with its kind | `tests/test_web_goals.py` |
| `goal_item()` reads a whole card; Hit, Drop and Reopen forms | `tests/test_web_goals.py` |
| The now figure and Plan line today equal the figures in `plan_line`; every other figure equals `show` of `get_goals`; the chart's description says `plan_line` word for word and never widens its own figures | T7 |
| The look at both widths | Owner's look |

### Foods

**Foods lists the saved foods as a table and saves one by hand in a fold-out. It is reached from the Food card and from Settings.**

#### Order, which is the markup order

1. Page head: h1 "Foods".
2. A grey line: "Foods you save are logged by name and amount: Spotter works out the kcal. Saving one by talking to your assistant works too."
3. The sample banner.
4. One strip: "Saved food saved." after a save, or the Undo strip "Saved food deleted." with Undo.
5. The Saved foods card.
6. The Save a food card.

- Laptop: Saved foods in columns 1 to 8, Save a food in 9 to 12.

#### The Saved foods card

- A row table: Food; Serving; kcal; Times logged; Grams (g); then Edit and Delete.
- The Food cell holds the name and, for a sample food, its sample tag: `<td class="lead">Dal bowl <span class="tag tag-sample">sample</span></td>`, as `test_web_account.py` reads it. (As built in step 4: `table.rows`, which stacks below 720 px, since the old table scrolled a phone sideways. The Food cell is `td.lead` and spans the row; Serving and each figure (`td.num`) carry a `data-label` shown above them.)
- Grams come out of the Serving cell into their own column, after Times logged, so the first three cells keep their place. A Grams column blank in every row is not shown.
- Empty: a dashed card, "No saved foods yet. Tell your assistant a food you eat often and its number, and ask it to save the food."

#### The Save a food card

- A fold-out "Save a food": Name; One serving is; Calories in one serving (kcal); Grams in one serving (g), with the hint "Optional"; "Save food".
- A grey line: "Saving a name you already have updates it."
- Closed by default. Open after an error, and open when there are no saved foods.

#### A saved food's edit page

- Page head: h1 "Edit {name}", a back link "Foods".
- One card as the edit pages: One serving is; Calories in one serving (kcal); Grams in one serving (g). The name stays: it is how a food is found.

| Check | Proved by |
|---|---|
| The table, the tag in the Food cell, the column order | `test_web_account.py`'s saved foods tests, on the row table as built in step 4, WEB |
| The fold-out's open and closed states | `test_page_rules.py`, WEB |
| The look | Owner's look |

### Settings

**Settings groups what only the person sets: the account, the time zone, the targets, what your assistant may do, and their data. Delete account goes through its own page.**

#### Order, which is the markup order

| # | Card | What it holds |
|---|---|---|
| 1 | Your account | A facts list: Name; Email; Time zone; Sign-in id; Account id. A grey line: "When your assistant is connected to this account, asking it who you are shows the same two ids." |
| 2 | Time zone | "Time zone saved." when saved. The grouped list, "Save time zone". The refusal in a `div.error` |
| 3 | Targets | "Targets saved." when saved. Calories a day (kcal); Lift sessions a week; Cardio a week (min); each error under its field. The add-back checkbox in a 44 px row: "Add cardio back: show a net total, food minus cardio calories". "Save targets" |
| 4 | What your assistant may do | "Your assistant's access saved." when saved. "Only you can change this, here: your assistant is never offered this switch." Three radio cards, each at least 44 px tall: the label in bold, its line under it. Labels from the one table: "Read only", "Read and log", "Read, log and delete". Their lines say "your assistant" (`web.py`'s switch table): "Read only: your assistant can read what you have logged and change nothing." and so on. As built in step 4, each line drops the label drawn in bold above it and says all its setting allows (`web.CLAUDE_CAN_LINES`): "Your assistant can read what you have logged and change nothing."; "Your assistant can also log and import entries, edit and restore them, save foods, set goals, and change your time zone and targets. It cannot delete."; "Your assistant can also delete entries and saved foods. A deleted one can always be restored." "Save" |
| 5 | Your data | "Export my data" (secondary) with its line. "Remove sample data" (secondary), when sample rows exist, with its line. "Delete my account", a link in `--err`, with "You see what goes before anything is deleted." |
| 6 | More | Links: Saved foods; Privacy (**not built in step 4:** the privacy page is not in this merge). A "Sign out" button, shown on a phone only: on a laptop the top bar carries it |

- Laptop: two columns of 6, the cards placed row by row in the order above.
- The page carries no code words. The account card no longer names a function.
- The switch's stored values and field stay as they are (`claude_can`: `read`, `log`, `log_delete`). Only the words a person reads change (section A, stage 7).

#### Delete my account, `/settings/delete`

- Page head: h1 "Delete my account".
- One card, at most 480 px wide, centred on a laptop.
- "This cannot be undone." in bold, then the counts list, its words from the one table ("Cardio sessions", not step 2's "Cardio entries" from `web.DELETE_COUNTS`), its markup kept as it is: `<li class="entry-line"><span>{what}</span><strong>{n}</strong></li>` (`test_web_account.py:321`).
- "Your settings go too, and so does your sign-in: signing in again starts a new, empty account."
- "To keep a copy, use Export my data on Settings first."
- "Delete my account" (danger), and "Keep my account" (a link back to Settings).

#### Sign out

- One POST form with the CSRF field, as today (`base.html:19-22`). On a laptop it sits at the right of the top bar. On a phone it sits in Settings' More card. The other copy is hidden by the stylesheet.

| Check | Proved by |
|---|---|
| The cards, the labels from the one table, the saved lines naming their subject | WEB, T3 |
| The delete page's counts structure and its form | `test_web_account.py:321` |
| One visible Sign out at each width | Owner's look |

### Landing page

**One line, one white button, one picture of it working, then the steps. The steps name no assistant. The counts on it come from the code.**

**The pages say "your assistant" wherever they said Claude.** The product reads as working with any AI assistant over MCP. It says so by how it is written, never defensively. This covers the landing page, the Settings card, every empty state and hint, the message pages and the page titles. It does not cover field names, stored values, or the text a connected model reads (section A, stage 7).

#### Order, which is the markup order

| # | Section | What it holds |
|---|---|---|
| 1 | Header | The brand, and a plain "Sign in" link |
| 2 | First screen | h1 "Spotter". The one line: "A fitness log you keep by talking to your assistant." The second line: "Tell your assistant what you lifted, ate or weighed. Spotter stores it and works out your bests, trends and goal pace in code, and your assistant quotes those numbers." A white "Sign in to start" to `/sign-in`, and a plain link "Source code on GitHub" |
| 3 | What it looks like | A card with the sample tag. The exchange as text first: "You" asks "Am I on track for my goals?"; "Your assistant" answers. Then one picture from `/static`: the body weight goal's Goals card alone, on sample data, its three rows "Now, the 7-day average", "Plan line today" and "At this pace" (as built in step 4: a chat inside a 720 px picture drew its text under 7 px on a phone, so the chat is the text alone). A caption: "Sample data. The assistant asked Spotter for the goals and quoted them. The Goals page shows the same numbers." |
| 4 | Before you start | "You need an AI assistant that can add a remote MCP server with sign-in." "Each assistant's plan limits are in the README's table." "New accounts are capped at {cap} a day." |
| 5 | How to start | `ol.steps`, three steps (below), then the list of assistants |
| 6 | Things to say first | Three sentences, each with what it does: "Am I on track for my goals?"; "How am I doing?"; "Lunch was two rotis and dal." Then "Everyday logging": three lines, "Back squat, 3 sets of 5 at 60 kg", "I weighed 72.5 kg this morning", "30 minutes on the treadmill" |
| 7 | Your data | You decide what your assistant may do: the switch's three settings by their one spelling, and where it starts. Export. Undo. Delete your account. A link "Read the privacy page" (**not built in step 4:** the privacy page is not in this merge) |
| 8 | How it is built | "{functions} functions your assistant can call, over MCP." "{cases} eval cases. Each checks the calls the server received, not what the assistant says it did." A link "Read the README" |
| 9 | Footer | Privacy; Source code; Contact; "Spotter is an open project, run by one person." (**Not built in step 4:** the Privacy link, since the privacy page is not in this merge.) |

- **One address for the repository.** `app/links.py` holds it once, as a constant. "Source code on GitHub", the footer's Source code, "Read the README" and the list of assistants go to it; the list goes to the README's "Your assistant" table. Contact goes to its issues page. No template types the address.

- The picture carries `width` and `height` and a short `alt`. The exchange text under it carries the words. (As built in step 4, the text sits above it.)
- The exchange's figures are the fixed-day sample goals of `tests/test_sample.py:170-192`: their labels, targets, dates, needed and actual paces and projected dates, through `show` and `day`.
- As built in step 4: the reply keeps the MCP server's own reply rules, one fact a line and no figure worked out, so it carries no pace. It opens "All three goals are on track by projection." Each goal then takes three lines: its label and date alone ("Body weight 77 kg by Tue 5 Jan 2027."), its `plan_line` as `get_goals` writes it, and "At this pace it is reached on {day}." (`web.LANDING_EXCHANGE`).
- "whoami" is not offered anywhere on the page.
- Signed out, the page links to no signed-in page (`test_web.py:162`).

#### The three steps

**Three steps, the README's three. No step names an assistant.**

| # | Step | Words |
|---|---|---|
| 1 | Sign in | "Your first sign-in creates your account." A "Sign in" link. Signed in: "You are signed in." and no link |
| 2 | Add Spotter to your assistant | "Add Spotter to your assistant as a remote MCP server. Some call it a custom connector. Its address:" and the `/mcp` address in `code.link-box`. "There is no key to paste: your assistant opens the sign-in page. Sign in with the same account you use here." |
| 3 | Say what you did | "Start a new chat and say what you did, or one of the sentences below." A small line: "The first time, your assistant may ask you to allow each call. Allow it." |

- Under the steps, one line: "To see it full first, press Add sample data on Today. One button on Settings removes it."
- Each step's `<li>` starts on a new line, indented four spaces, with no attribute, so `home_steps()` still splits it (`test_web.py:175`). No list nests inside a step.
- Without the server's address, step 2 shows "This server's address is not set up yet, so it cannot be connected." and no box (`test_web.py:212-218`).

#### The list of assistants

- After `ol.steps`, never inside it: `ul.assistants`, headed "How to add Spotter in each assistant".
- Its items are the README table's rows, in the README's order: Claude apps, Claude Code, ChatGPT, Cursor, VS Code with Copilot, Gemini app, Mistral, goose. Each item is its name as one link to the README's "Your assistant" table (the repository address from `app/links.py`, then `#your-assistant`).
- Then one line: "Any other MCP client that supports remote servers with sign-in is added the same way."
- The README's table is the one place each assistant's steps and limits live. When a row is added there, the list gains it.
- **Claude Code's item** holds its command in `code.link-box#claude-code-command` (`test_web.py:179`), with "Run this once, then type /mcp and pick spotter to sign in."
- **Claude's item** holds the Add to Claude button (secondary) only when section B's live click passed. Otherwise it is a link like the rest. **Not built in step 4:** the button waits for a check of the live click, so Claude's item is a plain link.
- The list names assistants as products. That is the one place the page says Claude.
- Without web sign-in, the white button gives way to "Sign-in on the web is not set up on this server yet."

#### Signed in

- The white button reads "Open Today" and goes to `/today`.
- A "Your pages" card follows Things to say first: a `dl.facts` of Today, Progress, Goals, Foods and Settings, each `<dt><a href="{path}">` with one line (`test_web.py:209`).
- Every other section stays.

#### Where the counts come from

| Count | Source | When read |
|---|---|---|
| Functions | The MCP server's own list, `len(mcp_server.NEEDS)`: the list T9 holds the README to | Once at start |
| Eval cases | The number of cases in `evals/cases.json` | Once at start |
| The new-account cap | `settings.signups_per_day` (`config.py:53`), 50 by default (`config.py:27`). At 0: "Spotter is not taking new accounts right now." (as built in step 4: "just" fails the page's own filler rule) | Each render |

- No count is typed into a template.
- If the cases file cannot be read, the eval line is left out. The page never shows a typed or a guessed number.
- No test count and no pass rate on this page. They are dated results: they belong in the README and the evals doc.

#### On a laptop

- First screen in columns 1 to 5, the picture card in 6 to 12.
- Before you start in 1 to 5; How to start in 6 to 12, spanning three rows.
- Things to say first in 1 to 5, then Your pages when signed in.
- Your data in 1 to 6, How it is built in 7 to 12.

#### Head furniture, this page only

- `<meta name="description">` with the one line and the second line.
- Link-preview tags: `og:title`, `og:description`, `og:type` (website), `og:url` (the site's address), `og:image` (the picture's full address), `og:image:alt`. **Not built in step 4:** `og:image` and its alt, since the picture is an SVG and no platform documents an SVG as a link-preview picture.

| Check | Proved by |
|---|---|
| Section order, the three steps, no assistant named in a step, no "Claude" outside the list of assistants, no "whoami", the footer links | T3 |
| The list of assistants sits after `ol.steps` and links to the README's table | `tests/test_web.py` |
| The cap from settings; the function count equals `len(mcp_server.NEEDS)`; the case count equals the cases file; the eval line gone when the file is missing | T3, a new case in `tests/test_web.py` |
| The exchange's figures equal `show` of the fixed-day `get_goals` | A new case in `tests/test_pages_text.py`, on the fixed day |
| `home_steps()` still splits; the command box keeps its id; `<dt><a href="{path}">` for the five pages, signed in | `test_web.py:175`, `:179`, `:208-209` |
| Link-preview tags on this page only | T3 |
| The Add to Claude button opens the filled dialog, when the list carries it | Live walk |
| The eval line shows on the live landing page, so the cases file ships with the deploy | Live walk, exit item 2 |
| The look signed out, at both widths | Owner's look |

### Privacy page

**Not built in step 4:** the page is not in this merge. Its text is written in `privacy.html`, not routed, and no page links to it.

**Plain facts in plain words. What is stored, where, who can see it, how to export it, how to delete it. It says that it is one person's project and not checked by a lawyer.**

- Route `/privacy`, open signed in and out, link name `privacy`. The signed-out frame, or the tab bar when signed in. The site footer.
- One column, at most 720 px wide. Sections as cards, each with an h2.

| # | Heading | What it says |
|---|---|---|
| 1 | In short | Spotter stores what you log so it can show it back to you and to your assistant. It shows no ads and runs no analytics. You can export everything and delete your account on Settings. This is one person's project. This page is not checked by a lawyer |
| 2 | What Spotter stores | Your name and email, from the sign-in provider. Your time zone, your targets and the switch for what your assistant may do. Lift sessions and their sets. Weigh-ins. Measurements: waist, hips, chest, arm, thigh, steps, sleep, resting heart rate, water. Food entries and saved foods. Cardio, with heart rate when you give it. Goals |
| 3 | Some of it is health data | Weight, resting heart rate, sleep and food are about your health. Spotter stores them so it can work out your trends |
| 4 | A log of calls | For each call, from your assistant or from the web: which function ran, when, how long it took, whether it worked and the size of the answer. Never what you said and never what came back |
| 5 | Where it is kept | Spotter runs on Railway, which holds the server and its database. WorkOS holds your sign-in |
| 6 | Who can see it | You. Your assistant, when you connect it: it gets what a function returns, under your own account with that assistant. The person who runs Spotter, who can reach the database to keep it running |
| 7 | Food lookups | When USDA lookup is switched on, the words of a food search go to USDA FoodData Central, with nothing about you |
| 8 | Export | Settings, Export my data: a zip of CSV files, deleted entries included |
| 9 | Delete | Deleting one entry hides it, and Undo brings it back. Delete my account on Settings removes every entry and your sign-in. It cannot be undone. The call log keeps its counts with no person on them |
| 10 | Cookies | One cookie keeps you signed in, for at most 14 days. One flag in your browser stops the time-zone check repeating. Nothing else |
| 11 | Contact | The repository's issues page, a link |
| 12 | This page | The date of this version |

- Every fact here is checked against the code before the push: `app/models.py` for what is stored, `app/calllog.py:3-4` for the call log, `app/web.py:84-86` for the cookie, `app/static/time-zone.js` for the flag, `app/config.py:54` for USDA lookup, `app/web.py:218` for the sign-in provider, `app/services/account.py:229-249` for delete.
- The page states only what the code and the settings in the repo show. It names no hosting region and no log retention.
- No code words: the cookie and the flag are described, not named.

| Check | Proved by |
|---|---|
| The page answers 200 signed in and out, with the page header and the frame | T3 |
| Text rules | T3 |
| Each fact against the code | Owner's read before the push |

### 404 and 500 pages

**Every error is a designed page in the same frame, with the page security header. No page says "404", "500" or "oops", or blames the person.**

#### The two designed pages

| Page | Status | h1 | Text | Links |
|---|---|---|---|---|
| Not found | 404 | "Page not found" | "There is nothing here." then "Go back to Today and start from there." | Today when signed in; Home and Sign in when signed out |
| A problem | 500 | "Something went wrong" | "Spotter could not finish this. If you were saving something, it may not be saved. Check the page and try again." | Home |

- "There is nothing here." stays: four tests read it (`test_web.py:1241`, `:1256`; `test_web_goals.py:166`, `:365`).
- The 404 page never repeats the address it was given, and never says whether a thing exists for someone else (`web.py:351-354`).
- **An unrouted path** gets the designed 404 with the page header. `/mcp`, `/.well-known/` and `/static` answer as they do now. An unrouted path carries no session, so its page uses the signed-out frame.
- **A crash on a web route** gets the designed 500 with the page header. Lane B traces where a 500 comes from today and says what it found before building the page. An error under `/mcp` answers as it does now. It uses the signed-out frame, since the failure may be in the session itself. The status stays 500 (`test_web.py:619`).

#### Every other message page

- One template for every message the web sends: Form expired (403), Too many changes (429), Already back and the sample refusals (409), Not saved and Not changed (400), Not set up yet (503), Your account is deleted (200), Sign-in failed (400), No new accounts today (403).
- One card, at most 480 px wide, centred on a laptop. h1 the title. The message. The fix in `--text-2`. The links as on the 404 page.
- The words stay as they are in `web.py` today, apart from the text rules and "your assistant" where they say Claude: "Meanwhile, ask your assistant to read your numbers back."

| Check | Proved by |
|---|---|
| An unrouted path, a routed 404 and a forced 500 each answer with the designed page, its status and the exact page header | T3 (furniture), a new case in `tests/test_web.py` |
| `/mcp` without a token still answers 401; `/.well-known/` still answers | `tests/test_web.py`, exit item 4 |
| The look | Owner's look |

### Favicon and titles

**Every page has a favicon and a title of the form "{Page} · Spotter".**

#### The favicon

| File in `app/static/` | What it is | Link in every page head |
|---|---|---|
| `favicon.svg` | The brand mark: the dumbbell cut out of a white rounded square | `<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">` |
| `favicon.ico` | The same mark, 32 by 32 | `<link rel="icon" href="/static/favicon.ico" sizes="32x32">` |
| `apple-touch-icon.png` | The same mark on black, 180 by 180 | `<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">` |

- All from `/static`, so `img-src 'self'` covers them.

#### Titles

| Page | Title |
|---|---|
| Landing | "A fitness log you keep by talking to your assistant · Spotter" |
| Today | "Today · Spotter" |
| Another day | "{day} · Spotter", the D12 form: "Mon 9 Nov", the year when it is not this year |
| Progress, Goals, Foods, Settings, Privacy | "Progress · Spotter" and so on |
| A lift, an activity | "{name} · Spotter" |
| An edit page | "Edit {kind} · Spotter" |
| Delete account | "Delete my account · Spotter" |
| 404, 500, a message | "{h1} · Spotter" |

- U+00B7 is the separator, with a space each side. No ISO date in a title.

| Check | Proved by |
|---|---|
| Every page, signed in and out, the 404 and 500 included: the title pattern and the three links | T3 (furniture) |
| The three files exist and answer 200 | `test_page_rules.py` |

### Motion

**Four kinds of motion, all from the stylesheet: the sheet slide, press feedback, charts drawing once on load, and a page cross-fade. All off under reduced motion. Nothing on a page replays after a save or a delete.**

#### What moves

| Motion | How | Length |
|---|---|---|
| Sheet slide | Popover with CSS transitions on `translate` and `opacity`, `@starting-style` for the way in, `allow-discrete` on `display` and `overlay`. Phone: up from the bottom. Laptop: a 32 px rise and a fade. The backdrop fades to black at 62% | 0.34 s, ease-out |
| Press feedback | `:active` scale: a tile 0.97, a button 0.98, a tab 0.96, a day pill's shape 0.92 | 0.08 s |
| Charts draw once on load | Each `series` line draws from start to end by `stroke-dashoffset` over `pathLength="1"`. Bars grow from their baseline, 0.05 s apart. The area fades in. The end dot appears after the line | Line 0.9 s; bars 0.5 s each |
| Page cross-fade | A cross-document view transition: `@view-transition { navigation: auto; }`. The tab bar holds still: it has its own `view-transition-name` | The browser's default |

- **The drawn state is the resting state.** Motion only ever runs from a start keyframe toward it. With no motion, every chart is complete.
- A chart inside a sheet draws in when its sheet opens, each time, since the sheet is not shown until then. Accepted: the person opened it. It is off under reduced motion and on a calm page, like every other chart.
- On a laptop each chart has a width cap in the stylesheet, so its text stays inside 11 to 14 px from 960 px up. Whether a capped chart sits well in its card at 1,280 px is judged in the step 4 walk.
- The cross-fade needs the incoming page's stylesheet from the browser's cache. Section H adds that. A first visit with an empty cache may not fade. That is accepted.
- A browser without view transitions or `@starting-style` shows the same pages with no motion.

#### Reduced motion

- Under `prefers-reduced-motion: reduce`: no transitions, no animations, and `@view-transition { navigation: none; }`.
- A sheet appears and goes at once, with no slide.
- On-load motion is written inside `@media (prefers-reduced-motion: no-preference)`, so it is opt-in.

#### The calm page: nothing replays after a save or a delete

**How the server marks it.** The body carries `class="calm"` when either holds:

| The page | Why it is a page after a write |
|---|---|
| Answers a POST: a form sent back with its error, status 400 | The person just pressed a save |
| Its address carries `saved`, `deleted`, `restored`, `notes`, `kind` or `add`, whatever the value | These are the marks the server puts on the page it sends a person to after a write (see the strips table under Today; Settings `?saved=`, `web.py:554`, `:589`, `:1073`; Foods `?saved=1` and `?deleted=`, `web.py:988`, `:1027`; Goals `?saved=` and `&notes=`, `web.py:1235`). `kind` is a goal-form segment and `add` opens a fold-out: the same page again |

- Every web write's redirect carries one of these marks. The writes that carry none today gain one: the day writes, Confirm and Undo (`web.py:788`, `:851`, `:911`, `:944`), sample data added and removed through `/today` (`web.py:1324`, `:1338`, `:598`), and the automatic time-zone save (`web.py:553`), which returns with `saved=time_zone`.
- A mark the page has no words for still makes the page calm and shows no strip.

**What the mark switches off.** Every on-load motion: the chart drawing, the bars, the area, the end dot, and the rolling number. Their rules all sit under `body:not(.calm)`.

**What it leaves on.** The sheet slide and press feedback. Both run only when the person acts.

**The cross-fade.** A body class cannot scope `@view-transition`, so the mark does not stop it. The cross-fade is the move between two pages, not motion on the landing page. Whether it runs after a form post is checked in the live walk. Either outcome is accepted.

#### Rolling numbers, only if they line up

- **Conditional.** Only on the Today card's one big number. Kept only if the digits land exactly on the plain number on the owner's phone. Dropped otherwise.
- The real number stays in the page as plain text, in flow. Copy, find and screen readers read it.
- Over it sit the digit columns, one per digit, each a strip of 0 to 9 moved by one of ten fixed classes. They carry `aria-hidden="true"`, `hidden` and the class `roll`. The stylesheet shows them only when motion is allowed and the page is not calm.
- Without the stylesheet, under reduced motion, or on a calm page, only the plain number shows.
- No script. T1 and T3 skip the digit columns by that marker.
- Cut second in section K.

| Check | Proved by |
|---|---|
| Every animation and the view transition sit under `prefers-reduced-motion: no-preference`; the reduce block turns off transitions, animations and view transitions | T5 |
| Every on-load animation rule is scoped to `body:not(.calm)` | T5 |
| A page fetched with each mark has `class="calm"`; a POST error response has it; a plain GET has not | `test_page_rules.py` |
| Every write's redirect carries a mark | WEB |
| Every `series` polyline carries `pathLength="1"` | `tests/test_charts.py` |
| The feel; the cross-fade after a post; reduced motion with nothing moving; a sheet's chart drawing in on each open | Live walk, owner's look |
| Rolling numbers line up | Owner's look on his phone |

### Figures and the fields that feed them

**Every figure and label the pages show, and the read that feeds it. Where no read returned one, stage 6 adds a field or cuts the figure. The web never works out a figure.**

Section E's reads are named by their section: E1 (`goals.goal_charts`, with `metrics.goal_path`), E2 (`trends.get_days`), E3 (`cardio.get_cardio`). Example figures are from `tests/test_sample.py`'s fixed day and name their read. They are the values the code returns before stage 6: section D rounds the named fields, and `show` rounds every figure it prints.

The Quantity column names the quantity kind `show` takes (section C). "Words" means the cell holds text, not a number. Each figure that had no read now names the field stage 6 adds; the gaps table at the end says which.

#### Today

| Figure or label | Read and field | Quantity | Today |
|---|---|---|---|
| The day's kcal | `get_log` day `food.total_kcal` (`food.py:118`, through `log.py:119`) | `kcal` | Exists |
| Target and kcal left | `food.target_kcal`, `food.remaining_kcal` (`food.py:120`) | `kcal`; `kcal_left` | Exists |
| Weigh-in chip | `get_log` day `weigh_in.kg` (`log.py:117`) | `weigh_in` | Exists |
| Lifts chip | `get_log` day `sessions`, empty or not (`log.py:115`) | Words | Exists |
| Cardio chip | `get_log` day `cardio[0].exercise`, `.minutes` (`cardio.py:74`) | `session_time` | Exists |
| New account | `sample_state.empty` (`sample.py:124`, `web.py:650`) | Words | Exists |
| Weight tile and sheet number | `get_trends` `bodyweight.smoothed` (`trends.py:64`). Fixed day: 79.22, shown "79.2" | `bodyweight_derived` | Exists |
| Rate, its two ends | `bodyweight.rate_per_week`, `rate_window.from`, `.to` (`trends.py:65-66`). Fixed day: -0.24, 2026-10-27 to 2026-11-10 | `rate`; the ends through `days` | Exists |
| No-rate reason | The trends note through `rate_note` (`web.py:1136-1139`) | Words | Exists |
| 7-day average and its basis words | `bodyweight.now` (`trends.py:64`); basis by `weight_now_label` (`web.py:1124-1133`). Fixed day: 79.01 | `bodyweight_derived` | Exists |
| Weigh-ins in 28 days | `bodyweight.weigh_ins_28d` (`trends.py:67`). Fixed day: 28 | `count` | Exists |
| Weight charts | `bodyweight.series[].smoothed` (`trends.py:61-63`) | `bodyweight_derived` | Exists |
| Calories tile, 7-day average | `calories.avg_7d`, `days_logged_7d` (`trends.py:83`). Fixed day: 2,216 over 7 | `kcal`; `count` | Exists |
| 28-day average and days | `calories.avg_28d`, `days_logged_28d` (`trends.py:84`). Fixed day: 2,196 over 28 | `kcal`; `count` | Exists |
| Share from estimates | `calories.estimated_share_pct_28d` (`trends.py:85`). Fixed day: 41 | `percent` | Exists |
| Calorie target | `calories.target_kcal` (`trends.py:86`) | `kcal` | Exists |
| The 7 calorie bars | E2 `days[-7:].kcal` | `kcal` | In E2 |
| Training tile | `sessions.this_week` (`trends.py:146`). Fixed day: 3 | `count` | Exists |
| Sessions a week, 4 weeks | `sessions.per_week_4w` (`trends.py:148`). Fixed day: 3.0, shown "3" | `count_avg` | Exists |
| Cardio this week and a week over 4 | `cardio.minutes_this_week`, `minutes_per_week_4w` (`trends.py:150-151`). Fixed day: 84.5 and 85.2, both shown "85" | `week_minutes` | Exists |
| Session and cardio targets | `sessions.target_per_week`, `cardio.target_minutes` (`trends.py:147`, `:150`) | `count`; `week_minutes` | Exists |
| Training sheet: four weeks | E2 `weeks[]` `to`, `lift_sessions`, `cardio_minutes` | `count`; `week_minutes`; `to` through `day` | Added in E2 (G1) |
| Day grid pills | E2 `days[].lift_sessions`, `cardio_entries` | Words; the day through `day` | In E2 |
| "{n} lift sessions" in the grid head | E2 `lift_sessions_28d`. Fixed day: 12, the sample's lift sessions (`test_sample.py:23`), all inside the 28 days | `count` | In E2 (G3) |
| Food entries | `get_log` day `food.meals` (`log.py:111`): `entry_dict` (`food.py:79-82`) and `saved_parts` (`food.py:90-94`) | `kcal`; `servings` | Exists |
| Serving words | `serving_text` (`web.py:670-686`) over `servings` and `serving_label` | `servings` | Exists |
| Food totals | `food.total_kcal`, `estimated_kcal`, `estimated_share_pct`, `net_kcal` (`food.py:118-121`) | `kcal`; `percent`; `net_kcal` | Exists |
| Sets | `get_log` day `sessions[].sets` (`lifts.py:72-73`) | `weight_kg`; `reps` | Exists |
| New bests | `sessions[].new_bests[].text` (`log.py:86`, `:115`) | Words, built by the service | Exists |
| Session notes | `sessions[].notes`, from `row_dict` | Words | Exists |
| Cardio figures | `cardio_dict`: `minutes`, `distance_km`, `pace.min_per_km`, `estimate.kcal`, `machine_kcal`, `notes` (`cardio.py:68-75`) | `session_time`; `km`; `kcal`. The pace is already the m:ss string from `formatting.clock` | Exists |
| Measurements | `get_log` day `measurements[]` `kind`, `value`, `unit` (`log.py:118`) | `cm`, `bpm`, `hours`, `steps` or `ml`, by kind | Exists |
| Day strips | The address marks (Motion) | Words | New marks, no figure |

#### Progress

| Figure or label | Read and field | Quantity | Today |
|---|---|---|---|
| Weight, Calories, Training tiles and sheets | As on Today | As on Today | Exists |
| Maintenance tile, sheet and row | `get_trends` `maintenance.kcal`, `intake_avg_kcal`, `weight_change_kg`, `days_logged`, `from`, `to` (`trends.py:153`). Fixed day: 2,513, shown "2,510"; 2,198; -0.86; 21; 2026-10-21 to 2026-11-10 | `maintenance`; `kcal`; `weight_change`; `count`; the window through `days` | Exists |
| No-maintenance reason | `maintenance_note` (`trends.py:154`, `metrics.py:507-516`) | Words | Exists |
| Body weight chart dots and line | `progress.bodyweight_chart(weigh_ins, goals, today, since)` `points[].kg`, `points[].smoothed` (`progress.py:182`), the last 28 days | `weigh_in`; `bodyweight_derived` | Exists; gains `since` (section D) |
| Body weight goal line | `progress.bodyweight_chart` `goal.target_kg`, `goal.target_date` (`progress.py:186-187`) | `goal_target`; the day through `day` | Exists |
| High, Low, Latest callouts | E2 `weight.high`, `weight.low`, `weight.latest`, each `{date, kg}` | `weigh_in`; the day through `day` | Added in E2 (G2) |
| Trend callout | `bodyweight.smoothed` | `bodyweight_derived` | Exists |
| Lift rows | `get_progress` `lifts[]` `exercise`, `current_estimated_max_kg`, `slope_per_week`, `last_trained` (`progress.py:108-112`). Fixed day: Back Squat 107.92, shown "107.9" (`test_sample.py:181`) | `estimated_max`; `rate`; the day through `day` | Exists |
| Best set | `lifts.history` `best_set` `weight_kg`, `reps` (`lifts.py:126-129`, read at `web.py:1087`) | `weight_kg`; `reps` | Exists for weighted lifts |
| A 0 kg lift's best set, "6 reps" | `lifts.history` `reps_only[lift].reps`, `.date`. The sample's Pull-Up is 3 x 6 at 0 kg (`stage-5.md:147`); Epley is None at 0 kg (`metrics.py:117`) | `reps` | Added (G8) |
| Why there is no estimated max | `current_estimated_max_kg` null, told apart by whether `reps_only` holds the lift | Words | Added (G8) |
| Cardio rows: sessions, time, fastest pace, last | E3 without an activity: `activities[]` `exercise`, `sessions_28d`, `minutes_28d`, `fastest_pace`, `last_date` | `count`; `week_minutes`; `fastest_pace` is already the m:ss string; the day through `day` | Added in E3 (G6) |
| A lift's page | `get_progress(lift)` `lift.current_estimated_max_kg`, `best_set`, `sessions`, `bests[]`, `estimated_max_over_time[]`, `trend` (`progress.py:43-58`) | `estimated_max`; `weight_kg`; `reps`; `count`; `rate` | Exists |
| Activity page tiles | E3 `bests.fastest_pace.pace`, `.date`; `bests.longest.distance_km`, `.date`; `bests.times[-1]` `distance_km`, `minutes`, `date` | `pace` is the m:ss string; `km`; `session_time`; the day through `day` | In E3. The note "4 runs at 5 km" is cut (G5) |
| Activity page sessions | E3 `sessions[]` `date`, `minutes`, `distance_km`, `pace`, `estimate.kcal`, `machine_kcal`, `is_sample` | `session_time`; `km`; `kcal`. `pace` is already the m:ss string | In E3 |
| Activity sessions' Notes column | E3 `sessions[].notes` | Words | Added in E3 (G7) |
| Pace chart | E3 `sessions[].pace_min_per_km`, `pace` | `pace`, from `pace_min_per_km` | In E3 |
| No-sessions note | E3 note, as `get_progress` gives it (`progress.py:139`) | Words | In E3 |

#### Goals

- Open goals read from E1 `goal_charts`: the goal numbers, unrounded, with `path` and `now_basis`. The keys below are the same in `get_goals`, rounded. Closed goals read from `get_goals(include_closed)` `closed[]`.

| Figure or label | Read and field | Quantity | Today |
|---|---|---|---|
| Label | `get_goals` `goals[].label` (`goals.py:291`, `:243-252`). Fixed day: "Bodyweight 77 kg" (`test_sample.py:173`); step 2 makes it "Body weight 77 kg" (`goals.py:252`) | Words, built by the service | Exists; the word changes in step 2 |
| Target date | `target_date` (`goals.py:297`). Fixed day: 2027-01-05, shown "Tue 5 Jan 2027" | The day through `day` | Exists |
| Now | `latest` (`goals.py:298`). Fixed day: 79.01; 107.92; 29.5 minutes, shown in m:ss | The goal unit's quantity (section C) | Exists |
| The words for what now is | E1 `goal_charts` goals' `now_basis`, by the rule `goals._source` uses (`goals.py:219-234`) | Words | Added in E1 (G9) |
| Against the plan line | E1 `plan_line` as written; the heading from `vs_plan` and `line_ended`; `days_ahead`, from `goal_charts` | Words, built by the service; days as `count` | In E1 |
| Plan line today | E1 `should_be_today` | The goal unit's quantity | In E1 |
| By projection tag | `status` (`goals.py:299`). Fixed day: "on track" for all three | Words | Exists |
| Not-enough-data line | `note` (`goals.py:304`, `metrics.py:349-357`) | Words | Exists |
| Started | `start_value`, `start_date` (`goals.py:296`). Fixed day: 79.9 on 2026-10-14; 93.33, shown "93.3"; 31.0 on 2026-10-17 | The goal unit's quantity; the day through `day` | Exists |
| Needed, So far | `needed_per_week`, `actual_per_week` (`goals.py:300-301`). Fixed day: -0.25 and -0.29; +1.01 and +4.41; -0.31 and -0.5 min, shown in whole seconds a week | `rate`; `time_pace` for a cardio time goal | Exists |
| At this pace | `projected_date` (`goals.py:302`). Fixed day: 2026-12-26; 2026-11-26; 2026-12-12 | The day through `day` | Exists |
| Chart plan and actual | E1 `path.plan`, `path.actual` | The goal unit's quantity | In E1 |
| Chart projection end | `projected_date`, `projection.projected`, `target_value` (`goals.py:302-303`) | The goal unit's quantity; `goal_target`; the day through `day` | Exists |
| Closed goals | `get_goals(include_closed)` `closed[]` `label`, `status`, `closed_at` (`goals.py:631-635`) | Words | Exists |
| Closed day in the person's zone | `closed[].closed_at` through `formatting.day_utc` | The day through `day_utc` | Added (G10) |
| Kind segments, field lists | `GOAL_KINDS`, the lift and cardio lists, `GOAL_MEASUREMENTS` (`web.py:163-166`, `:1262-1263`) | Words | Exists |

#### Foods, Settings, landing

| Figure or label | Read and field | Quantity | Today |
|---|---|---|---|
| Saved foods | `list_saved_foods` `foods[]` `name`, `serving_label`, `kcal_per_serving`, `grams_per_serving`, `times_logged`, `is_sample` (`food.py:451-453`) | `kcal_per_serving`; `grams`; `count` | Exists |
| Account facts | The user row: `name`, `email`, `time_zone`, `subject`, `id` (`models.py:110-116`) | Words | Exists |
| Targets | `calorie_target_kcal`, `target_sessions_per_week`, `target_cardio_minutes_per_week`, `add_back_exercise_kcal` (`models.py:117-121`) | `field` of `kcal`, `count`, `week_minutes` in the inputs | Exists |
| The switch | `claude_can` (`models.py:120`); its words (`web.py`'s switch table), relabelled to the one table and saying "your assistant" | Words | Exists |
| Sample rows | `sample_state.sample_rows` (`sample.py:124`) | Words | Exists |
| Delete counts | `_delete_counts` (`web.py:1364-1368`, labels `:191-193`) | `count` | Exists |
| Landing: functions | `len(mcp_server.NEEDS)` (`mcp_server.py:137`) | `count` | Exists |
| Landing: eval cases | The cases in `evals/cases.json` | `count` | Exists, read at start |
| Landing: new-account cap | `settings.signups_per_day` (`config.py:53`) | `count` | Exists |

#### The gaps, and what stage 6 adds for each

| # | Figure | Pick | Where |
|---|---|---|---|
| G1 | Training sheet's four weeks | `weeks: [{to, lift_sessions: int, cardio_minutes: float}]`, 4, oldest first; each the 7 days ending on `to`; the last ends today | E2 |
| G2 | High, Low, Latest beside the body weight chart | `weight: {latest, high, low} \| null`, each `{date, kg}`, over the live weigh-ins of the 28 days. A tie for high or low goes to the latest in the one order | E2 |
| G3 | "{n} lift sessions" in the grid head | None: `lift_sessions_28d` is in E2 already | E2 |
| G4 | The Today card's three chips | None: all in `get_log`. The lifts chip shows words, not a count | Exists |
| G5 | The activity page's three tiles | None: in E3. The third tile shows the last of `bests.times`. The longest tile's note shows its day; "4 runs at 5 km" is cut | E3 |
| G6 | Progress Cardio rows | E3's summary: `activities: [{exercise, sessions_28d: int, minutes_28d: float, fastest_pace: str \| null, last_date}]`, by name, every activity with a live session ever. `fastest_pace` is the best ever, the same as that activity's `bests.fastest_pace.pace` | E3 |
| G7 | The activity page's Notes column | `sessions[]` gains `notes` | E3 |
| G8 | A 0 kg lift's "6 reps", and the reason for no estimated max | `lifts.history` gains `reps_only: {lift: {reps, date}}` for a lift with no set above 0 kg: the most reps in one set. Progress and the lift page read it as they read `best_set` (`web.py:1087`). Web only: `get_progress` does not copy it, so no MCP result changes. Stage 7's reps measure for Claude builds on it | `lifts.history`, outside the three reads |
| G9 | The words for a goal's now | `now_basis`: one of `average_7d`, `latest_weigh_in`, `best_28d`, `latest`, worked out by the rule `goals._source` uses | E1 `goal_charts` |
| G10 | A closed goal's day | `formatting.day_utc(dt, zone, today)`, pure: the person's date of a UTC time, in the D12 form | `app/formatting.py` |
| G11 | The 7-day average's window dates in the weight sheet | Cut: the remark says "Last 7 days" | Cut |
