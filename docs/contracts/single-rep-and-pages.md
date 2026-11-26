# Single rep and two page lines contract

**Goal: a set of one rep counts as its own weight. The weight sentence on Today and Progress stops tying the 28-day count to the 7-day average. The Goals chart draws its projection along the fitted line it states.**

From the logic audit's second pass, 2026-11-26, read against `main` at `e23f05c`. Approved by the owner the same day, with the picks under "Decisions". Line numbers are from `e23f05c`: the builder reads each line again on the tree it starts from.

## For the owner

| What | Today | After |
|---|---|---|
| 100 kg for 1 rep | An estimated max of 103.3 kg | 100 kg |
| A goal of 102 kg with one rep at 100 as the only session | "reached" | "not enough data", until more sessions are logged |
| Today and Progress, the weight sheet | "The 7-day average is 80.9 kg, from 28 weigh-ins in 28 days." | "The 7-day average is 80.9 kg. 28 weigh-ins in the last 28 days." |
| Goals, the dotted projection | From today's "now" figure to the fitted value at the deadline, so its slope is not the stated pace | Along the fitted line, from its value today |
| Goals, a goal whose date has passed | A projection drawn back to the old date | No projection line |

Nothing an assistant reads changes: no description, schema, instruction, result field or line of `docs/can-and-cant.md`. Numbers in answers change where a single was the best set.

**No eval run is expected.** No migration, no new package, one deploy.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **A set of exactly 1 rep, above 0 kg, gives its weight.** 2 to 10 reps go by Epley as before. Nothing else about which sets count changes | A lift done once is that lift, not an estimate of it |
| 2 | **The four sentences an assistant reads that say "Epley, from sets of 1 to 10 reps" are not touched**: `app/services/fields.py:686`, `app/services/progress.py:46`, `app/services/goals.py:132`, `docs/can-and-cant.md:38`. They wait with the other wording | Each owes an eval run, and they stay close to true |
| 3 | **A goal already set keeps its start figure**, one set from a single included | A goal keeps what it was set with. Its "now" and its projection correct themselves on the next read |
| 4 | **The projection starts on the fitted line at today.** The ring at today stays on the "now" figure | The card states the fit's pace, so the drawing must be the fit |
| 5 | **A goal whose target date is before today draws no projection line** | A projection is never a history |
| 6 | **The end dot sits on the drawn line, on the day the fit reaches the target** | The dot marks the stated date |

## What a model reads, and the eval run

`python -m evals.need main` says yes when a description, a schema, the instructions, a resource or `whoami`'s answer moves, and when a string of 20 or more characters is added, changed or deleted in any `app/**/*.py` but `app/web.py` and `app/charts.py`. Docstrings, comments and templates are free.

So: no string of 20 or more characters is added, changed or deleted in `app/metrics.py` or `app/services/goals.py`. New keys are short. A builder that cannot build an item inside this line leaves it unbuilt and reports it. Expected at the end: no.

## Package P1: a single rep

### What exists

`app/metrics.py:118-128`, `epley(weight_kg, reps)`: `round(weight_kg * (1 + reps / 30), 2)` for 1 to 10 reps and a weight above 0, else none. Every estimated max is worked out from the logged sets on each read (`session_best`, `prs`, `lifts.history`, `get_progress`, a goal's `latest` and projection, `get_data`'s `estimated_max_kg`, the charts). None is stored for a set. A goal's `start_value` is stored.

### What changes

- `epley`: at exactly 1 rep, `round(weight_kg, 2)`. The guards and 2 to 10 reps are as they are. The function's notes and the module notes (`:12-14`) say so.
- `app/templates/hi.html:49`: "Each weighted set of 1 to 10 reps gives an estimated one-rep max. A single rep is its own weight, and more reps go by the Epley formula." The two numbers stay the template's own (`works.epley_min`, `works.epley_max`). The rest of the line is as it is.
- Nothing else in `app/`.

### Tests

- `tests/test_metrics.py:29`: `epley(100, 1)` is 100. Added: `epley(100, 2)` is 106.67; a single at 0 kg or below is none.
- Added, in `tests/test_metrics.py` or `tests/test_gym_logging.py`: after 90 kg x 2, a session of 96 kg x 1 gives the heaviest-weight best alone, and no estimated-max best (96 ties 96).
- Added, in `tests/test_goals.py`: a lift goal of 102 kg set with a given start before anything is logged, then one session of 100 kg x 1: it is not "reached", and its `latest` is 100.
- The builder also greps `tests/` and `evals/` for a test that works the estimate out itself (`/ 30`, `1 + 1 / 30`, sets of 1 rep) and lists each one it finds, moved or not.
- `tests/test_hi_there.py:144,200,610`: the page words, and the lower bound pinned at the weight.
- `tests/test_gym_logging.py:433`: the comment's figure.
- A test that goes red and is not named here is reported with its output, not rewritten to pass.

## Package P2: the weight sentence

### What exists

`app/templates/_parts.html:129`, shared by Today and Progress: after the rate sentence, "The {basis} is {now} {unit}, from {n} weigh-in(s) in 28 days." With none in the 28 days: "The {basis} is {now} {unit}. No weigh-ins in the last 28 days."

### What changes

"The {basis} is {now} {unit}. {n} weigh-in(s) in the last 28 days." One is "1 weigh-in in the last 28 days." The none branch is as it is. Nothing else on the sheet moves, and no service changes.

### Tests

`tests/test_web.py:5311,5447,5515,7745,7751`. `:5466,5481` hold as they are.

## Package P3: the Goals projection

### What exists

- `app/metrics.py:316-355`, `projection`: a least-squares fit (`_fit`, `:357-371`) of the goal's points from the last 42 days. It gives `slope_per_week` and `projected` at 2 decimals, `projected_date`, and `latest`, which is the "now" figure and not a point of the fit.
- `app/services/goals.py:484-524`: with `charts`, a goal also carries `path`, `now_basis`, `line_ended`.
- `app/charts.py:620-631`, `_projection_end`, and `goal_svg` (`:656-703`): the line runs from (today, now) to (projected date, target), or to (target date, projected value). Past a cap on the date it is cut by a share of that segment, and its end value is held inside the grid.

### What changes

1. **`app/metrics.py`**: one new function beside `slope_per_week`, `fit_today(points, today)`: `(the fitted value at today, the slope a day)`, both unrounded, from the same `_fit`; none where `projection` draws no line.
2. **`app/services/goals.py`**, in the `if charts:` block only: `numbers["fit"] = {"today": value, "per_day": slope}`, or none. No other result gains a key: `get_goals`, `set_goal`, `get_progress` and `get_data` answers are byte for byte as before.
3. **`app/charts.py`**, the goal chart. With `f(d) = fit.today + fit.per_day x (d - today in days)`:
   - No line when: there is no `fit`; or there is no `projected_date` and no `projection.projected`; or the target date is before today (decision 5); or the end day is today.
   - The line starts at (today, f(today)).
   - The end day is the `projected_date` when there is one, else the target date. Past the cap (as today: the later of tomorrow and the target date plus half the span from the first plotted date to the target date), the end day is the cap.
   - The end value is f(end day). So the line is one straight piece of the fit, at every length.
   - The grid is worked out as today, from the plan, the actual points, the target and the "now" figure: the fit adds nothing to it, so no chart's ticks move. Where either end of the line is outside the grid's values, the line is cut where it meets the grid's edge: that end moves along the line, and the slope is kept. A value is never moved alone. With no part of the line inside the grid, no line is drawn.
   - Units: f is worked out in the stored unit, and each end's value then goes through the chart's own `shown` (`formatting.convert`), as every other drawn value does. So a lift or weight goal in lb is drawn in lb, and a cm, bpm or time goal as stored.
   - The description (`_goal_desc`) is not touched: it keeps the real figures and the projected date, with or without a line, as it does today for a capped line.
   - The end dot is at the line's end.
   - `goal_svg` has one caller, `app/web.py:3050`, the Goals page. The landing's picture is a static file (`app/static/landing-goals.svg`) and is not redrawn in this work.
   - The ring at today stays on `latest`. The actual line, the plan line, the target tick, the labels, the description text and the legend's rule are as they are.
4. The lift page's own chart (`chart_svg`) is not touched.

### Tests

- `tests/test_charts.py:435-575`: the start is the fitted value at today, not the ring; added: with a `latest` off the fit, the drawn slope equals `fit.per_day` in the drawn unit, to within 1% (worked back from the line's two ends through the chart's own scales), for a body weight goal in kg, a lift goal in lb, a cm goal, and a cardio time goal (lower is better); the cap and the grid edge, at either end, keep that slope; a passed target date draws no line; an end day of today draws none; a chart's ticks are the same with and without `fit`.
- `tests/test_web_goals.py:1968-2103` and `tests/test_page_rules.py:1711`: the page's line no longer begins at the ring; fake chart rows gain `fit`.
- `tests/test_goals.py:951-983`: `goal_charts` carries `fit`; `get_goals`' goals do not.
- The description pins (`tests/test_charts.py:459,514,995`) hold as they are.

## Package D: dated notes

One note at the foot of each, under `## Note, 2026-11-26: single rep and two page lines (docs/contracts/single-rep-and-pages.md)` with the file name in backticks. Nothing above a note is rewritten.

| Contract | The note says |
|---|---|
| `first-day.md` | `:46-47`, `:143`: a set of 1 rep gives its weight; 2 to 10 go by Epley. The page line as P1 leaves it |
| `data-on-request.md` | `:178`: `estimated_max_kg` on a row of 1 rep is the weight. The field's own words are not changed (decision 2) |
| `stage-6.md` | `:1386`: the lead's last sentence as P2 leaves it. `:473-497`, `:1305-1317`: the projection as P3 draws it, and `fit` as chart-only data |

## Work packages

One builder, one tree, in this order: P1, P2, P3, D. P1 and P3 share `app/metrics.py`.

| | P1 | P2 | P3 | D |
|---|---|---|---|---|
| App files | `app/metrics.py`, `app/templates/hi.html` | `app/templates/_parts.html` | `app/metrics.py`, `app/services/goals.py` (the `if charts:` block), `app/charts.py` | the three contracts |
| Test files | `test_metrics`, `test_gym_logging`, `test_goals`, `test_hi_there` | `test_web` | `test_charts`, `test_web_goals`, `test_page_rules`, `test_goals` | none edited |

After the last edit, once, plain and then with `-W error`: every test file that names a symbol changed (by grep over `tests/`) and each package's test files, and for D the docs tests (`test_docs`, `test_web`, `test_switch`, `test_text_rules`, `test_evals`, `test_data`). Never the whole suite.

## Eval cases

None added, changed or removed. Read in `evals/cases.json`: no case fixes a single's estimated max, and no case judges a page.

## The exit check

1. Each package's tests are green, plain and with `-W error`.
2. `git diff main -- app/mcp_server.py app/mcp_schemas.py app/guide.py app/log_shape.py docs/can-and-cant.md app/services/fields.py app/services/progress.py` is empty.
3. `python -m evals.need main` says no.
4. The whole suite, once, on a clean export of the commit, before the push.

Not checked in this work: the Goals page in a browser.

## Known limits

| Limit | Why it stands |
|---|---|
| Four sentences an assistant reads still say "Epley, from sets of 1 to 10 reps" | Decision 2 |
| A lift goal set earlier from a single keeps a start figure about 3% high | Decision 3 |
| A keyed write's saved answer replays its old numbers for a day | The retry rule: the same key gets the first answer back |
| The lift page's chart draws its own projection, from its own points | Not in this work |

## Notes from the build, 2026-11-26

Built by one builder, then one review on a frozen copy with probes, then one fix round.

| Line above | As built |
|---|---|
| P1, the page line | Two sentences, not one: the page rule holds a sentence to 25 words. "Each weighted set of 1 to 10 reps gives an estimated one-rep max. A single rep is its own weight, and more reps go by the Epley formula." |
| P2's tests | `tests/test_units_pages.py` also pinned the old sentence, in kg and lb, and changed with it |
| P3, "with no part of the line inside the grid, no line is drawn" | And such a line does not reach the chart's dates: the review found a hidden far projection still stretched them. Fixed, with a test |
| P3, a projected date before today | Treated as an end day of today: no line |
| D, `data-on-request.md` | The note says the figure is shown at the estimated max's own step (1 decimal in `get_data`), not at 2 decimals |

The exit check's last line, "Not checked in this work: the Goals page in a browser", was overtaken: after the commit the pages were seen in a browser from a local render, as the build log says. The two-sentence page line and the fix for the hidden projection were made after the review and were read by no reviewer; the tests and the whole suite hold them.

The review's other point stands as it is: on the Goals page test, the legend is checked against the line the chart drew, so a chart that left out a line it should draw would pass that one test. `test_the_four_week_charts_on_the_fixed_day` pins the three lines that must be there.
