# First day contract: the setup page, and setup led by the assistant

**Goal: a new person says "Hi Spotter, help me set up" and their assistant walks them through it, one question at a time, asking only for what the account still lacks. The page behind `/hi` says the same in order, then what they can say, what Spotter works out and how, what it keeps and does not do, and what stays their own. Every number on the page comes from the code.**

It builds on the hi there contract (`docs/contracts/hi-there.md`), which still binds what this one does not change. Section 10 lists what changes in it.

## 1. His words

2026-11-15, morning: "The hi page isn't quite there. we should have a proper setup page. That way they will know, like, okay, fine, I can tell them my goals, I can tell them this, I can tell them that. This is what I should do on the first day of using it. And then, like, what are our functions and what are our features? Not don't outright spell it out as functions. Tell what and all you can say easily. What is tracked, how it's tracked, how close am I to my goals. So you can use regression, projections, and so on. Things like that, right?"

2026-11-15, later: "encourage people to say hi spotter. Ask the app to help me set up, things like that, right? How do we set it up cleanly? Of course, when I say set it up, doesn't mean connect the MCP, because if you're seeing the high, there's a good chance you've already set up the MCP." And: "encourage them to save their fixed routines if needed. goals, and aso on. whatever would improve the exp."

Earlier words that still bind it: onboarding "AI native only through the AI itself, but maybe share a link that they can read through what are the capabilities". "how to port information from outside, how to set it up properly, what are the things that you can really use". "a smooth UX". "Let's not overcomplicate things out of the bag". "the logbook and the shape and the general theme and all that is very strong".

**The owner's go.** He read the page's wording (section 4) and the decisions (section 3) on 2026-11-15. He answered that its eval run should be one run with the gym logging work's: "Run it together. I don't see an advantage of running it separately". The lead took that as his go to build, with the wording open to change until the merge.

## 2. What exists today

Read on 2026-11-15 at `6282bf1`, the commit this is built on: what is live (`698ee18`) plus the gym logging contract as a doc. The lift logging rows are from `docs/contracts/gym-logging.md`, which is built beside this work on a tree of its own.

| Piece | State | Where |
|---|---|---|
| The page | `/hi`, open signed in and out, the same words for everyone. Four cards: say it, what Spotter keeps, bring your history over, more | `app/templates/hi.html:1-64`, `app/web.py:1130-1137` |
| Its facts | Landing lines, the say-list and the keeps counts are constants and counts from the code. No digit is typed in the template | `app/web.py:1023-1025`, `:1060-1065`, `:1121-1125` |
| Its rules | 17 decisions and R1 to R16. No form, no function name, no account data, "your assistant" never "Claude" | `docs/contracts/hi-there.md` |
| Its tests | 30 test functions, 58 with their parameters | `tests/test_hi_there.py` |
| The text rules on a page | Only `×` and the no-break space beyond ASCII. No filler (please, simply, just, easy, easily, quickly, really, actually, basically), no chat words, no AI words, no "not just", no semicolon, no "&" or "@" for a word, no ISO date, no snake_case and no function name, no sentence over 25 words, no "none" or "0" standing for a figure | `tests/text_rules.py:141-149`, `:212-260`, `:298-303`, `:408-430`, `:446-449` |
| What a new account's assistant reads | `whoami` carries `links.hi_there` always and `new_account_note` while the account is empty | `app/services/account.py:64-69`, `:96-103`. `app/mcp_schemas.py:249-252`, `:282-285` |
| "Empty" | No live row in the five entry tables, no live saved food, no open goal, one small read each, stopping at the first found | `app/services/account.py:106-111` |
| whoami's shape | Its result forbids a top-level field it does not name | `app/mcp_schemas.py:15`, `:262-263` |
| The instructions | 3,927 characters, 169 under Claude Code's 4,096 cut. Pinned in four test files | `app/mcp_server.py:183-208`, `tests/test_switch.py:545-547` |
| Text that sends a person to a page | One allowed: the fix of `not_allowed`. A test reads every model text for "page" or "form" beside an action word | `app/mcp_server.py:244-245`, `tests/test_mcp.py:1171-1183` |
| MCP prompts | None. The server has twenty functions for everyone and two resources | `app/mcp_server.py:1-2`, `:1174-1192` |
| Setup guidance | None. Each step can be saved today: `update_settings` (time zone, three targets), `log_bodyweight`, `log_lifts`, `set_goal`, `save_food`, `import_entries` | `app/mcp_server.py:227-237`, `app/services/account.py:164-170` |
| The targets | Three columns on the user row, null when not set | `app/models.py:122-124` |
| Routines | **None.** No table, no column, no function holds a workout's name or its usual sets | `app/models.py` |
| Lift logging, on this tree | Each `log_lifts` call makes a session | `app/services/lifts.py:255-258` |
| Lift logging, being built | A date's sets go into that date's session, call by call. `new_session` true starts a second | `docs/contracts/gym-logging.md`, decisions 1 and 2 |
| Evals | 26 cases. One message a run. A fresh account with a time zone set | `evals/cases.json`, `evals/judge.py` |
| The published function list | 20 functions for everyone, 143,999 bytes as the tests' app sends it. With the owner's three, 156,439 | Measured on `6282bf1` |

### What Spotter really works out, so part 3 of the page is true

| Figure | What the code does | Where |
|---|---|---|
| Estimated max | Epley: weight x (1 + reps / 30), sets of 1 to 10 reps only | `app/metrics.py:72`, `:121-131` |
| A new best | A session's best Epley, or its heaviest weight, above every earlier set of the lift. A first session sets the mark | `app/metrics.py:164-182`, `app/services/lifts.py:72-80` |
| A lift now | The best session of the last 28 days, else the latest session's | `app/metrics.py:73`, `:140-155` |
| Body weight now | The 7-day average, else the latest weigh-in | `app/metrics.py:77`, `:387-394` |
| **The weight trend** | **A smoothing, not a fitted line:** an exponentially weighted moving average, alpha 0.1. Its rate a week is the smoothed line's change between the latest weigh-in (in the last 7 days) and the last one 14 to 42 days ago | `app/metrics.py:80-82`, `:513-550`, `app/services/trends.py:72-85` |
| **The fitted line** | A least-squares straight line through the last 42 days of a goal's own points (raw weigh-ins, session estimated maxes, measurements, cardio times). Needs 4 points on 2 dates. It gives a goal's pace so far, where it lands on the date, and the day it reaches the target. A lift's trend on its own uses it too | `app/metrics.py:74-75`, `:291-336`, `app/services/goals.py:233-243`, `:390-413`, `app/services/progress.py:55` |
| Needed pace | The steady change a week from now that reaches the target by its date | `app/metrics.py:278-288` |
| Status | reached. on track when the line's value on the date meets the target. behind. not enough data | `app/metrics.py:330-333`, `app/services/goals.py:393-398` |
| Projected date | The first day the line meets the target. None when flat, pointing away, or over 730 days out | `app/metrics.py:86`, `:339-358` |
| The plan line | A straight line from the goal's start to its target, by their dates: where it is today, ahead or behind and by how much | `app/metrics.py:459-508` |
| Calories | Average a logged day over the last 7 and 28 days, and the share of the 28 days that is estimates | `app/services/trends.py:44-45`, `:88-103` |
| Cardio calories | MET x 3.5 x body weight now / 200 x minutes. No weigh-in, no estimate | `app/metrics.py:187-190`, `docs/can-and-cant.md:128-136` |
| Maintenance | Intake average over the logged days of the last 21, less the 7-day weight change over 21 days x 7,700 / 21. Needs food on 14 of 21 days and a weigh-in in each 7-day window | `app/metrics.py:83-85`, `:577-602` |

**The gap against the owner's words.** He said "regression, projections". The code does fit a regression line, a least-squares straight line, for goals and for a lift's trend. The weight trend itself is a smoothed line, not a fitted one. The page says each as it is.

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **One page, `/hi`, still "Hi there".** Link name `hi_there`, template `hi.html`, title "Hi there · Spotter" | His name for it. The assistant already hands `/hi`, and two eval cases read "/hi" |
| 2 | **The page's first words after the head: say "Hi Spotter, help me set up."** It never speaks of connecting: a person reading it is connected, and "More" keeps the one line for those who are not | His words |
| 3 | **Five parts, in this order:** your first day. what you can say. what Spotter works out. what Spotter keeps and what it does not do. your say. Then "More" and the footer. Each a card with its h2 | What he named, in his order |
| 4 | **The same words for everyone.** A visitor's load reads no database. Nothing of the person's is on it. Their own checklist is the assistant's to walk, from `whoami` | Hi there decisions 2 and 3 stand. An open page can be passed on |
| 5 | **No digit and no list typed in the template.** Every saying is a constant in `web.py`. Every number in part 3 is a constant of `app/metrics.py` or `app/services/trends.py`, passed by the view. Each typed word that states a fact has a test, named in section 4 | The page cannot fall behind the code |
| 6 | **The first day's steps are setup's own steps,** drawn from one tuple, `account.SETUP_STEPS`, so the page's order is the order the assistant asks in | One order for the page and the talk |
| 7 | **Setup guidance rides in `whoami`, as a `setup` field.** Weighed in section 6 | `whoami` opens every conversation in every assistant. No new function, no room needed in the instructions |
| 8 | **The routine step and the routines lines are drawn only while `save_routine` is offered** (`"save_routine" in mcp_server.NEEDS`, at each render, as the feedback line is). The `routine` setup step in `whoami` follows the same rule, at each call. A test holds both states | The page is right before routines are built and after |
| 9 | **Built beside gym logging, joined after.** This work is built on `6282bf1`, where each `log_lifts` call still makes a session. Gym logging is built on a tree of its own from the same commit, and the two are joined after both are reviewed. This work changes no line of gym logging's. The line "Sets said one by one go into that day's session." stays as worded and holds once the two are joined. Its test reads `log_lifts`' description for the gym logging contract's words. On this work's own tree that one test fails. It passes at the join. Nothing in the repo hides it | The owner asked for one eval run over both. A test that fails until the join cannot be forgotten at the join |
| 10 | **The landing's "Your data" lines become one constant both pages draw.** The landing's words do not change and its word-for-word test passes unedited. No new link from the landing to `/hi` | One place for each fact. The landing is not in this work |
| 11 | **The instructions do not change: 3,927 characters.** | No room. The new words sit in `whoami`'s description and result |
| 12 | **A new account's assistant offers setup once,** in the new account note, beside the link. Otherwise setup comes up only when the person asks | His "encourage people". The note already speaks once |
| 13 | **Setup asks for no measurement and says nothing of the switch.** Waist and the rest stay in "What you can say". The switch stays on the page's part 5 | Five minutes. A model text that sends a person to a page is held to one exception |
| 14 | **Nothing new is stored.** `whoami` stays a read. A skipped step is not remembered: it is asked again the next time the person asks to set up | No column, no migration. "Once" stays the assistant's, as for the link |
| 15 | **An eval run of the whole set follows the join,** one run for this work and gym logging. Section 8 | `whoami`'s description, a new result field and the note are text a model reads in every conversation. The owner asked for one run |

## 4. The page's full wording, with its sources

`<q>` marks a thing to say, as the landing marks it. A no-break space sits before each unit. "Drawn" means the built page reads it from the code. "Typed" means the template or a constant holds the words and a test holds them true.

### Head

| Line | Source |
|---|---|
| h1 "Hi there" | Typed, as today |
| "A fitness log you keep by talking to your assistant." | Drawn: `web.LANDING_LINES[0]` (`app/web.py:1023`) |
| "To start, say this to your assistant:" <q>Hi Spotter, help me set up.</q> | Typed: a constant `web.SETUP_SAY` |

The landing's second line leaves the head: part 3 says what it said, in full.

### Part 1. Your first day

| Line | Source |
|---|---|
| h2 "Your first day" | Typed |
| "Your assistant asks one thing at a time, and saves each answer as you give it." | Typed. Held by the setup note's words (section 7): one question at a time, save each answer |
| "Skip any step. Say it again another day, and it asks only for what is still missing." | Typed. Held by the setup test: `missing` lists only steps the account lacks |
| 1. **Where you stand.** <q>I weigh 82 kg. Yesterday I squatted 3 sets of 5 at 80 kg.</q> "Spotter keeps the weigh-in, and the session's best becomes the mark to beat." | Saying: constant. Steps `weigh_in`, `lift_session`. "the mark to beat": `lifts.first_text` (`app/services/lifts.py:72-80`), a test calls it |
| 2. **A goal with a date.** <q>I want to weigh 78 kg by the end of March.</q> "Spotter keeps where you start, and works out the pace a week the goal needs." | Step `goal`. `set_goal` stores the start (`app/services/goals.py:18-26`). `needed_per_week` (`app/metrics.py:278-288`) |
| 3. **Your targets.** <q>2,200 kcal a day, 4 lift sessions a week, 150 minutes of cardio a week.</q> "Each day's food is shown against the calories, and each week against the other two." | Steps `calorie_target`, `sessions_target`, `cardio_target`. Ranges hold the saying: `rules.NUMBERS` via `update_settings` (`app/services/account.py:12-14`). The week against targets: `app/services/trends.py:169-174`. The day: `docs/can-and-cant.md:124-126` |
| 4. **Foods you eat often.** <q>Save roti: one roti is 120 kcal.</q> "After that, two rotis are logged from the saved food, not from a guess." | Step `saved_food`. `save_food`, `FOOD_RULE` (`app/mcp_server.py:150-155`, `:936-942`) |
| 5. **Your fixed routines.** <q>Save my push day: bench press 3 sets of 8 at 60 kg, then dips 3 sets of 10.</q> "After that, <q>log my push day</q> logs those sets." | Drawn only while `save_routine` is offered (decision 8). Step `routine` |
| 6. **Your old data.** <q>Here is the export file from my old app.</q> "Your assistant shows a preview first, and is told to write only when you say yes." | Step `import`. The live page's import words, held by the same test as today: `mcp_server.IMPORT_RULE` (`app/mcp_server.py:93-99`) |
| small: "If your assistant does not know your time zone, it asks once which city you are in." | Step `time_zone`. `TIME_ZONE_NOTE` (`app/services/account.py:64-66`): a test reads "ask once which city" |

The list's order is the setup steps on offer, less `time_zone`: a test joins each item's step names and compares.

### Part 2. What you can say

| Group and line | What comes back | Source |
|---|---|---|
| h2 "What you can say". Label "Log it" | | Typed |
| <q>Back squat, 3 sets of 5 at 60 kg</q> | "Sets said one by one go into that day's session." | `SAY_EVERYDAY[0]` (`app/web.py:1064`). The line: gym logging decision 1. A test reads `log_lifts`' description: the one test that waits for the join (decision 9) |
| <q>I weighed 72.5 kg this morning</q> | | `SAY_EVERYDAY[1]` |
| <q>Lunch was two rotis and dal.</q> | "Logs the meal. Each number is tagged: you said, saved food, USDA or estimate." | `SAY_FIRST[2]` (`app/web.py:1062-1063`) |
| <q>30 minutes on the treadmill</q> | "Its calories are estimated from your own body weight." | `SAY_EVERYDAY[2]`. `log_cardio` (`app/mcp_server.py:958-967`) |
| Label "Ask". <q>Am I on track for my goals?</q> | "Each goal against its plan line, and the date it is reached at this pace." | `SAY_FIRST[0]` |
| <q>How am I doing?</q> | "Your weight trend, calories a day, lift sessions, cardio and maintenance." | `SAY_FIRST[1]` |
| <q>How is my bench press going?</q> | "Its best each session, its new bests and its trend." | `get_progress` (`app/services/progress.py:52-69`) |
| <q>What did I do this week?</q> | "Each day's lifts, food, weigh-in, measurements and cardio, up to 14 days at a time." | `get_log` (`app/services/log.py:3-9`). 14 drawn: `mcp_server.LOG_DAYS[1]` (`app/mcp_server.py:260`) |
| Label "Fix or undo". <q>That was 85 kg, not 80.</q> | "The entry is changed where it stands." | `edit_entry` keeps the id (`docs/can-and-cant.md:158-161`) |
| <q>Undo that change.</q> | "The entry's last change is put back, once." | `restore_entry` with `undo_edit`, one step (`docs/can-and-cant.md:162-164`) |
| <q>Delete yesterday's lunch.</q> | "Your assistant shows you the entry and asks first." | `DELETE_RULE` (`app/mcp_server.py:142-148`), the two calls |
| <q>Bring back the lunch I deleted.</q> | "Nothing is erased, so it comes back." | `restore_entry` (`docs/can-and-cant.md:168-172`) |
| Label "Goals". <q>I want to bench 100 kg by the end of June.</q> | "A lift goal is an estimated one-rep max." | `set_goal` (`app/mcp_server.py:646-651`). Month end: `MONTH_END_RULE` |
| <q>I hit my squat goal.</q> | "The goal is closed as reached." | `set_goal` status hit (`app/mcp_server.py:681-682`) |
| Label "Tell the maker". <q>Send feedback: the weight chart confuses me.</q> | "It reaches the person who runs Spotter." | Drawn while `send_feedback` is offered, as today's line (`app/web.py:1127`, `:1136`). It leaves "More" |

Bringing data in is step 6 of part 1. It is not said twice.

### Part 3. What Spotter works out

| Line | Source, and what the view passes |
|---|---|
| h2 "What Spotter works out" | Typed |
| "Spotter works out every number in code, from what you logged." | Typed. The landing's own claim (`app/web.py:1024-1025`) |
| "Your assistant is told to quote them. The one number it may estimate is a food's calories, and it must say so." | Typed. A test reads the instructions: "never estimate your own, except a food's kcal" (`app/mcp_server.py:191`) |
| **Bests.** "Each weighted set of 1 to 10 reps gives an estimated one-rep max, by the Epley formula." | 10: `metrics.EPLEY_MAX_REPS`. 1: ruling R6. "Epley": a test calls `metrics.epley(100, 10)` and gets 133.33. "weighted" (the review's L1): a test holds that `metrics.epley` at 0 kg, the weight a bodyweight set is sent at, gives none |
| "A new best beats every earlier set of that lift, by that estimate or by weight." | Typed. A test reads `metrics.prs`' two kinds |
| "Your lift now is your best session of the last 28 days, or your latest before that. One light day does not pull it down." | 28: `metrics.RECENT_BEST_DAYS` |
| **Body weight now.** "The average of your weigh-ins in the last 7 days, or your latest when that week has none." | 7: `metrics.BODYWEIGHT_AVG_DAYS` |
| **The weight trend.** "A smoothed line through every weigh-in: each one moves it a tenth of the way." | "a tenth": a test holds `metrics.EWMA_ALPHA == 0.1` |
| "Its rate a week needs a weigh-in in the last 7 days and one from 14 to 42 days ago." | `metrics.RATE_LATEST_DAYS`, `metrics.RATE_REFERENCE_DAYS` |
| **Goal pace.** "Each goal shows the pace a week it needs to make its date, and your pace so far." | Typed. `needed_per_week`, `actual_per_week` (`app/services/goals.py:409-410`) |
| "Your pace so far is a straight line fitted through the last 6 weeks, from at least 4 points on 2 days." | 6: `metrics.PROJECTION_DAYS // 7`. 4: `metrics.PROJECTION_MIN_POINTS`. 2 (the review's L2): no constant holds it, so the dict holds it, and a test calls `metrics.projection` with 4 points on one day and gets none |
| **Where you land.** "That line gives the day you reach the target at this pace." | `projected_date` |
| "It says on track when the line reaches the target by your date, and behind when it does not." | Typed. A test calls `metrics.projection` both ways |
| "There is no day when the line is flat, points away, or gets there more than 2 years out." | 2: `metrics.PROJECTED_MAX_DAYS // 365` |
| **The plan line.** "A straight line from your start to your target, by their dates." "It says how far ahead of it or behind it you are today." | Typed. A test calls `metrics.goal_path` |
| **Calories.** "Your average a day over the last 7 and 28 days, counting only days with food logged." "It says how much of that is estimates." | 7 and 28: `trends.WEEK_DAYS`, `trends.WINDOW_DAYS` |
| **Cardio calories.** "From the activity's MET value, the minutes and your own body weight now." "With no weigh-in there is no estimate." | Typed. A test calls `metrics.cardio_kcal` and `log_cardio` with no weigh-in |
| **Maintenance.** "About what you eat to stay the same weight: your food, less your weight change, over 21 days." | 21: `metrics.MAINTENANCE_DAYS` |
| "It needs food on 14 of those days, a weigh-in in the last 7 days and one from 21 to 27 days ago." | 14: `MAINTENANCE_MIN_LOGGED`. 7: `BODYWEIGHT_AVG_DAYS`. 27: `MAINTENANCE_DAYS + BODYWEIGHT_AVG_DAYS - 1` |
| "It is an estimate, and it never changes your target." | Typed. `trends.TRENDS_HOW` says so (`app/services/trends.py:59-60`) |

### Part 4. What Spotter keeps, and what it does not do

| Line | Source |
|---|---|
| h2 "What Spotter keeps" | As today |
| "Lifts. 41 lifts by name, each session with its sets." "Cardio. 15 activities." "Body. Weigh-ins, and 9 measurements: waist, hips, chest, arm, thigh, steps, sleep, resting heart rate and water." "Food. What you ate, by meal: breakfast, lunch, dinner and snack." "Goals. 4 kinds: body weight, lift, cardio time and measurement." | Drawn as today: `web.HI_KEEPS` (`app/web.py:1121-1125`) |
| "Targets. Calories a day, lift sessions a week and cardio minutes a week." | Typed. A test holds `web.TARGET_FIELDS` (`app/web.py:217`) to these three |
| "Routines. Your fixed workouts by name, each with its lifts and usual sets." | Drawn only while `save_routine` is offered |
| Label "What it does not do" | Typed |
| "It gives no medical advice, and knows nothing of injury, pain, form or recovery." | `docs/can-and-cant.md`, "What it cannot tell you". A test reads those lines |
| "It logs weights in kg and distances in km. An import from another app converts pounds and miles." | `SetIn.weight_kg`, `log_cardio`'s `distance_km`. Import units (`docs/can-and-cant.md:192-196`). A test reads the schemas' field names |
| "It counts calories, not protein, carbs or fat." | `FoodEntry` has `kcal` only (`app/models.py:350-375`). A test holds the columns |
| "It knows lifts by its own list of 41 names. A name not on the list is refused, never guessed." | 41 drawn: `len(exercises.LIFTS)`. `rules.unknown_exercise` |
| "It sends no reminders and no email, and does not sync with a phone or a watch." | **Typed, and only searched:** no match for smtp, sendmail, remind or notif in `app/` at `6282bf1`. A test holds that no function name in `NEEDS` holds remind, notify, email or sync. A later feature that adds one fails it |

### Part 5. Your say

| Line | Source |
|---|---|
| h2 "Your say" | Typed |
| "**You decide what your assistant may do.** A switch on Settings has two settings: "Read, log and delete" and "Read and log". It starts at "Read, log and delete"." | The landing's line, word for word, from one shared constant `web.YOUR_DATA` (decision 10). Labels: `CLAUDE_CAN_LABELS` (`app/web.py:263`), default `models.CLAUDE_CAN[0]` |
| "**Export.** Settings gives you everything you have logged, as a zip of CSV files." | The landing's line (`app/templates/home.html:135`) |
| "**Undo.** Right after a delete, Undo brings the entry back. Your assistant can restore an entry it deleted." | The landing's line (`:136`) |
| "**Delete your account.** On Settings. It removes every entry and your sign-in, and it cannot be undone." | The landing's line (`:137`) |

### More, and the footer

As today, but the feedback line moved to part 2: "Not connected yet? The steps are on the home page." "Each assistant's own steps are in the README's table." Signed in: "Your pages start at Today." and "Open Today". Signed out with web sign-in: "Sign in to see what you logged." and "Sign in". The site footer.

### The text rules

The wording was run through the page rules of `tests/text_rules.py` when it was drawn up, 119 pieces: no finding was reported, and the longest sentence was 23 words. The build's own page sweeps are the proof: they are in the exit check. Where a line fails a rule as built, the builder stops on it and the lead takes it to the owner: the wording is his.

### Size

The live page is four cards, about one laptop screen. This one is six cards, about two screens: **not checked in a browser.** It scans by its bold first words and the labels of part 2.

## 5. What changes around the page

| Text or piece | Stays true as it is? | So |
|---|---|---|
| `links.hi_there`'s description: "A short read for the person: what to say, what Spotter keeps and how to bring history over from another app. Give it when new_account_note is present, or when the person asks what Spotter can do." | Yes. The page still says those, and more | **Unchanged.** It already hands the link on "what can you do?" |
| `new_account_note`'s field description | Yes | Unchanged |
| `new_account_note` | No: it must offer setup | Changed, section 7 |
| `whoami`'s description | Needs one sentence for `setup` | One sentence added, section 7 |
| `docs/can-and-cant.md` "A new person" | Yes | Unchanged, every line. A new section "Setting up" follows it, section 7 |
| The instructions | Yes | Unchanged: 3,927 |
| The landing | Yes | Its "Your data" lines move into `web.YOUR_DATA`. Its words and its word-for-word test do not change |
| Two stale sentences a model reads | No | Corrected in this package, section 7 |
| `tests/test_hi_there.py`, 58 cases | | Expected when the wording was drawn up: none goes, about 14 change, the rest stay. Among those that stay: no digit typed, every count from its source, no database read signed out, the same `main` for everyone, the page limit, the route tables, the instructions' length and the two eval cases. The builder's report names each test it changed, with why |
| New tests | | The steps' order and each step's rule (section 6), on a fresh database, in each state. `setup` is absent once only `import` is missing. `whoami` and Today still agree on "empty". Each part 3 number equals its constant. The typed facts named in section 4. The routine lines and the `routine` step in both states of `NEEDS`. The gym logging line against `log_lifts`' description. The two eval cases judged by hand both ways |

## 6. Where setup guidance lives

| Way | Reaches every assistant? | Text | Verdict |
|---|---|---|---|
| (a) A `setup` field in `whoami`'s result | Yes: `whoami` opens every conversation, by the instructions | One sentence in `whoami`'s description, a short `how` in the result, the step names | **Chosen** |
| (b) A new read function, `get_setup` | Only when the model picks it from "help me set up". A running chat does not see a function added after it began | A description, a result, a 21st function on the landing's count | Not chosen: one more thing to choose, and no gain over (a) |
| (c) An MCP prompt | Only in apps that show prompts. Which apps do: not checked | A prompt's text | Not chosen as the way. Could be added later beside (a) |
| (d) The can-and-cant resource alone | Only when a client reads resources. Most do not by themselves: not checked | A section | Not chosen alone. It carries the same rule, as for the link |

**How "missing" is worked out.** One private helper in `services/account.py`, `_held(s, user)`, answers each question with one small read. Both "empty" and the setup list read its answers, so `whoami` and Today never disagree on "empty". Ruling R10 says how.

| Step, in order | Done when the account holds | Saved with |
|---|---|---|
| `time_zone` | a time zone that is a real zone: the test `whoami` already makes for `time_zone_note` (`app/services/account.py:99`) | `update_settings` |
| `weigh_in` | a live weigh-in | `log_bodyweight` |
| `lift_session` | a live lift session | `log_lifts` |
| `goal` | an open goal | `set_goal` |
| `calorie_target` | `calorie_target_kcal` not null | `update_settings` |
| `sessions_target` | `target_sessions_per_week` not null (0 counts as set) | `update_settings` |
| `cardio_target` | `target_cardio_minutes_per_week` not null | `update_settings` |
| `saved_food` | a live saved food | `save_food` |
| `routine` | a live routine. Listed only while `save_routine` is offered. Until routines are built nothing can hold one | `save_routine` |
| `import` | a live row of any entry table with `logged_by` "import" | `import_entries` |

- `setup` is carried while a step other than `import` is missing. So "import" never keeps it alive alone, and a person who has done the rest is not asked about old apps in every conversation.
- The reads: the seven "empty" asks today, each now asked whatever the others answer, plus up to five for `import` (stopping at the first found). Twelve small reads at most, each on `user_id` with `limit(1)`.
- A read: no write, no lock, no write limit, as today.

## 7. Texts old and new

| Where | Old | New |
|---|---|---|
| `whoami` description, appended | (ends) "If it carries new_account_note, do what it says." | adds: "If it carries setup, walk setup.missing as setup.how says." |
| `WhoamiResult.setup`, new, `{missing, how}` | None | Description (with "other than import" from the review's L3): "Present while a setup step other than import is not done yet. missing: those steps, in the order to ask them: time_zone, weigh_in, lift_session, goal, calorie_target, sessions_target, cardio_target, saved_food, import. how: when and how to ask." |
| `setup.missing`, its own description (R11) | None | "The setup steps not done yet, in the order to ask them." |
| `setup.how`, its own description (R11) | None | "When to bring setup up, and how to ask." |
| `setup.how`, new, one constant `SETUP_HOW` | None | "Bring setup up only when the person asks, or when new_account_note says to offer it. Ask for the first step in missing, one question at a time, and save each answer with its function as soon as it is given. The person may skip any step: go to the next. Ask for nothing that is not in missing." |
| `NEW_ACCOUNT_NOTE` | "Nothing is logged on this account yet. Give the person links.hi_there once, last in your reply: it is a short read on what to say, what Spotter keeps and how to bring history over from another app. It has nothing to fill in." | "Nothing is logged on this account yet. Offer once to set Spotter up together, as setup.how says. Give the person links.hi_there once, last in your reply: it is a short read on how to set up, what to say and what Spotter works out. It has nothing to fill in." |
| `docs/can-and-cant.md`, new section "Setting up", after "A new person" | None | "- While a setup step other than the import is not done, `whoami` carries `setup`: `missing`, the steps not done yet in order, and `how`." "- A step is done when the account holds it: a time zone, a live weigh-in, a live lift session, an open goal, each target set, a live saved food, a live imported row." (the review's L3: "a live") "- Claude walks the steps only when the person asks to set up, or once on a new account. One question at a time. Any step can be skipped, and comes back the next time the person asks." |
| `WhoamiResult.targets` description, a stale sentence (R14) | "The person's targets. Not used by any function yet." | "The person's targets." |
| `docs/can-and-cant.md`, "What it cannot tell you", first line, a stale word (R14) | "- It knows nothing about injury, pain, sleep, recovery, form or health." | "- It knows nothing about injury, pain, recovery, form or health." |

The model texts pass the rules for text Claude reads and hold neither "page" nor "form". The doc lines pass the doc rules. Run on 2026-11-15 with the repo's own `tests/text_rules.py`: no finding. The `whoami` sentence and the note name no page.

### The setup talk, step by step

Five minutes, on an empty account with a time zone set. Nothing is asked twice, nothing is required.

| # | The assistant asks | The person might say | Saved with | The assistant says back |
|---|---|---|---|---|
| 0 | (only with `time_zone_note`) "Which city are you in?" | "Bangalore" | `update_settings` time_zone Asia/Kolkata | The summary: "Time zone set to Asia/Kolkata. Today is ..." |
| 1 | "What do you weigh at the moment?" | "82 kg this morning" | `log_bodyweight` 82 | The weigh-in, from its result |
| 2 | "Any lift you did lately? Say the sets." | "Squat 3 by 5 at 80 yesterday" or "skip" | `log_lifts` with the date | "First Back Squat session: estimated max ... is the mark to beat." |
| 3 | "What is one goal, and by when?" | "78 kg by the end of March" | `set_goal` bodyweight, the month's last day | The goal's needed pace and plan line, from its result |
| 4 | "Do you aim for calories a day, lift sessions a week or cardio minutes a week?" | "2,200, 4, and no cardio target" | `update_settings`, the two given | The summary's lines |
| 5 | "What do you eat most days? Give one serving's calories if you know them." | "Roti, 120 a roti. Dal, not sure." | `save_food` for roti. Dal is not saved: a saved food needs its calories | "Saved Roti. Dal can be looked up when you log it." |
| 6 | (once routines exist) "Do you have a fixed workout, like a push day? Say its lifts and sets." | "Push day: bench 3 by 8 at 60, dips 3 by 10" | `save_routine` | The routine as saved |
| 7 | "Do you have history in another app? Its export file brings it over." | "Later" | nothing | What is set, then the link, last |

## 8. Evals

An eval run of the whole set follows the join: `whoami`'s description, a new result field and the note are text a model reads in every conversation. It is one run for this work and gym logging, as the owner asked. Three cases are added here, the third, `setup_stays_quiet`, from the review's S1: 29 on this tree, 32 with gym logging's three. The last whole run of 26 cost $12.78 at list price (the gym logging contract), so 32 are about $15.75, 3 runs each on `claude-opus-5-5`. The cost is told to the owner before it runs.

```json
{"id": "setup_asks_first",
 "say": "Hi Spotter, help me set up",
 "expect": {"function": "whoami", "reply_asks": true, "reply_has": ["weigh"],
            "no_call": ["log_bodyweight", "log_lifts", "set_goal", "update_settings", "save_food", "log_food",
                        "log_cardio", "log_measurement", "import_entries"]},
 "db": [{"table": "bodyweight", "count": 0}, {"table": "goal", "count": 0}]}

{"id": "setup_picks_up",
 "say": "Help me finish setting up Spotter",
 "seed": [{"as": "weigh_in", "service": "log_bodyweight", "data": {"kg": 80.9, "date": "{{yesterday}}"}},
          {"as": "goal", "service": "set_goal",
           "data": {"kind": "bodyweight", "target_value": 77, "target_date": "{{next_month_end:3}}"}}],
 "expect": {"function": "whoami", "reply_asks": true, "reply_has": ["lift"],
            "no_call": ["log_bodyweight", "set_goal", "log_lifts", "update_settings", "save_food", "import_entries"]},
 "db": [{"table": "bodyweight", "count": 1}, {"table": "goal", "count": 1}]}

{"id": "setup_stays_quiet",
 "say": "I weighed 80.4 kg this morning",
 "seed": [{"as": "earlier", "service": "log_bodyweight", "data": {"kg": 80.9, "date": "{{yesterday}}"}}],
 "expect": {"function": "log_bodyweight", "inputs_include": {"kg": 80.4},
            "no_call": ["log_lifts", "set_goal", "update_settings", "save_food", "import_entries"],
            "reply_lacks": ["?"], "reply_lacks_pattern": ["\\bset(ting)?[ -]?up\\b"]},
 "db": [{"table": "bodyweight", "count": 2,
         "rows": [{"id": "{{seed.earlier}}", "kg": 80.9}, {"kg": 80.4, "date": "{{today}}"}]}]}
```

| Case | Proves | Does not prove |
|---|---|---|
| `setup_asks_first` | On an empty account, "help me set up" writes nothing before the person answers, and the reply asks about the first missing step | That the talk goes on well: a run is one message |
| `setup_picks_up` | With a weigh-in and a goal held, the reply asks about the next step, a lift, and writes nothing | That it never asks again for a weigh-in: "lift" may appear beside it |
| `setup_stays_quiet` | With `setup` in `whoami` and nobody asking to set up, the weigh-in is logged and the reply asks nothing, speaks of no setup and writes no other step | That it stays quiet over a longer talk: a run is one message. A setup prompt worded with no question mark and no "set up" passes it |

`hi_there_first_log` and `hi_there_not_again` stay as they are: the note still says to give the link once, last.

## 9. Ruled where the decisions leave the how open

| # | Ruling | Fills |
|---|---|---|
| R1 | **The base.** Built at `6282bf1`. 26 eval cases become 29. 20 functions for everyone, as before. Every count written is the count on this tree | The start |
| R2 | **Gym logging's lines are not touched.** Nothing in `app/services/lifts.py`. Nothing of `log_lifts` or `LogLiftsResult` in `app/mcp_server.py` and `app/mcp_schemas.py`. None of the lines of `docs/can-and-cant.md` that the gym logging contract's texts table names: the "Lift sessions" line, the "Duplicates" and "Sending a write twice" lines, and the `log_lifts` row of the function table. Nothing in `app/calllog.py`, `evals/judge.py` or `evals/run.py` | 9 |
| R3 | **The session line's test.** One test function of its own, holding one thing: `log_lifts`' published description holds "each later call adds its sets to it", the gym logging contract's words. It is written plainly: no skip, no expected-failure mark, no condition. It fails on this tree and passes at the join. Every table of test runs names it as the one test that waits for the join | 9 |
| R4 | **The frame.** The view stays `hi_there`, and what hi there R2, R3, R7, R13 and R14 say of it stands: it reads the signed-in person as the landing does and nothing else, builds no link with `links.page`, sits behind no page limit, and `main` is the same bytes for everyone but the one line and link of "More". Six cards, in order, each a `section.card` with its h2: `hi-first-day`, `hi-say`, `hi-works`, `hi-keeps`, `hi-yours`, `hi-more`. Only classes the stylesheet already has. `style.css` is not edited | 3, 4 |
| R5 | **Where the words live.** Every saying, the words in a `<q>`, is a constant in `web.py`, and a saying the landing also draws is the same object, not a copy. Each list the code defines is drawn from it. The prose of a line may be typed in the template, as "What Spotter keeps" is today, or held in a constant beside its saying. No constant holds markup, and no template turns escaping off. The template types no digit | 5 |
| R6 | **The numbers of part 3** reach the template as one dict the view passes, each value read from the constant section 4 names, and each drawn through `show("count")`. The Epley line's lower end, 1, has no constant: the dict holds it beside `EPLEY_MAX_REPS`, and a test holds both ends to `metrics.epley` itself, which gives no value at 0 and at 11 reps and gives one at 1 and at 10. A test holds that 42 days are whole weeks and that 730 days are 2 years, so "6 weeks" and "2 years" are exact. `app/metrics.py` and `app/services/trends.py` are not edited | 5 |
| R7 | **The 14 of part 2** is drawn from `mcp_server.LOG_DAYS[1]` into the constant that holds the line | 5 |
| R8 | **`web.YOUR_DATA`.** Four pairs, the bold words and the sentence. The switch's sentence is built from `CLAUDE_CAN_LABELS` in `models.CLAUDE_CAN`'s order and the column's default, as the landing's view reads them today. Both templates loop over it. The landing's words are the same, and its word-for-word test passes unedited. In its HTML the six quote marks of that sentence are now written as `&#34;` by the template engine: a browser shows the same. A test holds the landing's card to the parent's four lines, read as words, and to the parent's markup once `&#34;` is read as a quote mark | 10 |
| R9 | **The steps on offer.** `account.SETUP_STEPS` is the ten names in order, `routine` between `saved_food` and `import`. A step that needs a function, today `routine` alone with `save_routine`, is on offer only while that function is among the names it is given: `account.setup_steps(offered)` gives the steps on offer, in order. The page calls it with `mcp_server.NEEDS` at each render. The `whoami` function passes `NEEDS` to the service at each call: `services.whoami(user_id, offered)`, and with nothing given no such step is on offer. No service imports the server | 6, 8 |
| R10 | **`_held` and the reads.** `_held(s, user)` answers the seven questions "empty" asks, each with one small read on `user_id` with `limit(1)`, all seven whatever the others answer. `whoami` calls it once: the account is empty when none is held, and the steps read the same answers. Today's `account_empty` keeps its early stop and asks the same seven questions through the same one-question code, so the two cannot disagree and Today costs what it costs now. The three targets and the time zone are read from the user row. `import` is asked only when another step is missing, of the entry tables that hold a live row, stopping at the first found. A test counts `whoami`'s statements: 13 selects at most, the user row and 12, and nothing else. `whoami` takes no lock and no write limit, and writes nothing | 7, 14 |
| R11 | **`setup` in the result.** `{"missing": [step names], "how": SETUP_HOW}`, after `new_account_note`, present only while a step other than `import` is on offer and missing. `missing` holds the steps on offer that are not done, in `SETUP_STEPS`' order, `import` among them when it is not done. A `Setup` model in `mcp_schemas.py` with `missing` and `how`, each with its own description (section 7). `WhoamiResult.setup` is optional, default none, left out when absent. The step names in its description are the steps on offer on this tree: a test compares them | 7 |
| R12 | **The page's list and the steps.** `web.py` holds the first day's items in one constant, each with the setup steps it stands for. An item is drawn while every one of its steps is on offer. The same call decides the "Routines" line of part 4 | 6, 8 |
| R13 | **can-and-cant.** "A new person" keeps every byte. "Setting up" goes between it and "What Claude may do", with the three lines of section 7. No line is added to "A new person": the third line of "Setting up" says the offer | 5 |
| R14 | **Two stale sentences are corrected here,** since `whoami`'s text changes anyway and the eval run follows. `WhoamiResult.targets` said "Not used by any function yet" while `get_trends` compares the week against them (`app/services/trends.py:169-174`): the sentence goes. The resource said Spotter knows nothing about sleep while it records sleep hours (`docs/can-and-cant.md:10-11`): the word goes. The fewest words that make each true | The lead's brief |
| R15 | **The old schema.** The known limit is held by a test against `whoami`'s result model as it was published before this work: a result that carries `setup` fails it on `setup` alone, and a result with every step but `import` done passes it | Known limits |
| R16 | **The eval cases' place.** In `evals/cases.json` the two go straight after `hi_there_not_again`, and their two rows in `docs/evals.md` straight after its row, so they sit apart from what gym logging adds at the end. The third, `setup_stays_quiet`, goes last, and its row last. What they prove is section 8's table, not repeated in `docs/evals.md` | 15 |
| R17 | **Notes kept true.** The module notes of `web.py`, `services/account.py` and `mcp_schemas.py`, and the comment at the top of `hi.html`, say what is now so | |
| R18 | **Measured.** The published function list before and after, in bytes, and which hashes moved: `whoami` alone | Exit check |

## 10. What changes in the hi there contract, and what stands

| Hi there | Now |
|---|---|
| Decision 4, the page's sections and words | Section 4 of this contract |
| Decision 6, "Nothing on it speaks of the switch" | Part 5 says the landing's own line on the switch, with no link. The rest of decision 6 stands: no form, field, button or script in `main`, no step done on the page, no function name, no assistant named, no account data, no link-preview tags |
| Decision 9, the note's words | Section 7. The field's description, and when the note comes and goes (decision 10), stand |
| Decision 11, `whoami`'s description | One more sentence after it. The instructions' length is 3,927 since the switch |
| Decision 13, "A new person" in can-and-cant | Stands, every line. "Setting up" follows it |
| Decision 14, the feedback line | Still drawn only while `send_feedback` is offered, read at each render. It is now a line of part 2 |
| R4, the say-list's two constants | Stand, and the landing still draws them. The page draws their sentences in part 2 |
| R10, the old schema | R15 of this contract |
| Everything else | Stands: decisions 1, 2, 3, 5, 7, 8, 10, 12, 15, 16, and R2, R3, R5 to R9, R11 to R14, R16 |

## 11. Where it meets the gym logging work

Both are built from `6282bf1`, each on a tree of its own. Neither tree holds the other. The join is made after both are reviewed.

| Point | Gym logging | This work | At the join |
|---|---|---|---|
| `log_lifts` | Its rule, description, arguments and result change | Not touched | The session line's test passes |
| `app/mcp_server.py`, `app/mcp_schemas.py` | `log_lifts`, `LogLiftsResult` | `whoami`, `WhoamiResult`, `Setup` | Different functions |
| The published list's hashes (`tests/test_feedback.py`) | `log_lifts`' moves | `whoami`'s moves | Both move |
| `docs/can-and-cant.md` | The lines R2 names | One word of "What it cannot tell you", and a new section | Each keeps its own lines |
| `evals/cases.json`, `docs/evals.md` | Three cases | Three cases, placed by R16 | 32 cases |
| The case count: `README.md`, `tests/test_evals.py`, `tests/test_feedback.py`, `tests/test_switch.py` | 26 becomes 29 | 26 becomes 29 | 32, moved again by hand |
| The eval run | A run follows its build | A run follows this one | One run of 32 covers both |
| The page | Not touched | "Sets said one by one go into that day's session." | True from the join on |

## 12. Work package: one builder, FD1

| Files | Exit check |
|---|---|
| `app/templates/hi.html`, `app/templates/home.html` (the "Your data" lines from `web.YOUR_DATA` alone), `app/web.py` (the constants, the view's arguments, the notes), `app/services/account.py` (`SETUP_STEPS`, `SETUP_HOW`, `setup_steps`, `_held`, `whoami`, the note), `app/mcp_schemas.py` (`Setup`, `WhoamiResult.setup`, the `targets` description), `app/mcp_server.py` (`whoami`'s one sentence and what it passes), `docs/can-and-cant.md`, `evals/cases.json`, `docs/evals.md` (two rows), `README.md` (the case count), `tests/test_hi_there.py`. Other test files only where a decision makes an assertion fail, each named in the report | Every decision and ruling has a test. `/hi` answers 200 signed out, signed in, with web sign-in off and with no server address, with its words as section 4 says in each state, the routine lines in both states of `NEEDS`. No digit typed in the template. Every part 3 number equals its constant. The page sweeps and the text rules pass on `/hi`. A visitor reads no database. `whoami` carries `setup` with the steps in order and only the missing ones, on a fresh database and after each step is done. It is absent when only `import` is missing. Another person's rows change nothing. `whoami` and Today agree on "empty" in every state of the live page's tests. `whoami` writes nothing and takes no lock. Every new field is described. The new texts pass the message rules and the page-sending test. The instructions are 3,927 characters. No function but `whoami` changed in the published list. The landing's words are the parent's. The two eval cases load and judge as designed. The tests for the files touched pass, plain and with `-W error`, but the one test that waits for the join (R3) |

## 13. Known limits

- **A client that holds the old function list.** `whoami`'s published schema forbids a field it does not name. After the deploy, an account with a step missing gets `setup`, so a conversation begun before the deploy may refuse `whoami`'s result. That is most accounts, the owner's among them, until a new conversation. No real client was tried.
- **A skipped step comes back** each time the person asks to set up. Nothing stores a skip.
- **A person who does not lift, or counts no calories,** always has that step missing, so `setup` rides in each `whoami` for them. Its words say to bring it up only when asked.
- **`whoami` reads more.** Up to 12 small reads after the user row, where it made up to 7. While a step is missing and the person has never imported, the `import` question reads through that person's live rows of each entry table and finds none.
- **"Once" is the assistant's,** for the offer as for the link.
- **Other clients.** The evals run through Claude Code. Whether another client follows `setup.how` the same way is not proved.
- **The page's length** is not checked in a browser.
- **The no-reminders line** rests on a search of `app/`, and a test on function names.
- **The landing's HTML** differs from before by six quote marks written as `&#34;` (R8).
- **The landing's visible text changes in one place only:** its eval case count, which is drawn from `evals/cases.json` (`web.EVAL_CASES`), so it moves from 26 to 29 with the cases this work adds. Every other word is the parent's.
- **Until the join,** the page's line on sets said one by one is ahead of the code on this work's own tree, and its test fails there (decision 9).

## 14. Not in this work

Routines themselves: a saved workout by name, saved and read through the assistant. They are their own contract and package, after gym logging.

Until routines are built, the routine step, the page's two routine lines and the `routine` setup step are not drawn (decision 8).

Also not in this work: an MCP prompt. A setup function. A per-person checklist on the page. Measurements in setup. The switch in setup. A link to `/hi` from the landing, Today or the footer. Any look, stylesheet rule or picture. A stored mark that setup was offered or a step skipped. Any line of gym logging's. The README's line on the page and the build log's entry, done after the review.
