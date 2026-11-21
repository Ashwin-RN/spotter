# What Spotter can and cannot tell you

Spotter is one person's own record of their training. It stores what they log and works out numbers
from it. It knows nothing else.

## What it records today

- **Lift sessions:** the date, optional notes, and the sets: exercise, weight, and reps, or a time, or a
  distance and time. For a pair of dumbbells, one hand's weight. Sets logged for a date go into that date's session, call by
  call. A second workout that day is a second
  session when the person says so.
- **Body weight:** one weigh-in a day.
- **Measurements:** nine kinds, each with its own unit: waist, hips, chest, arm and thigh in cm,
  steps, sleep_hours in hours, resting_hr in bpm, and water_ml in ml. One value per kind a day.
- **Food:** what was eaten, its calories, its meal, and a tag saying who supplied the number.
- **Saved foods:** foods the person eats often, with a serving and its calories.
- **Routines:** workouts the person does often, saved by name: each lift with its sets and reps, and a
  weight when the person gave one.
- **Cardio:** the activity, minutes, and distance, incline, speed, heart rate and the machine's
  calories when given.
- **Goals:** a target to reach by a date, of four kinds (below).
- **Targets:** calories a day, lift sessions a week and cardio minutes a week. A weekly amount is a
  target set with `update_settings`, not a goal: it has no start and no date.

Exercise and cardio activity names come from one shared list. A lift can be logged in the person's own
words: Spotter takes the usual lift, or the one the person has logged that fits the set, and asks when two
fit. Every other function takes names as `list_exercises` gives them.

## What its numbers are

- **Logged facts:** sets, weights, reps, weigh-ins and dates are what the person logged. Nothing more.
- **Estimated max is an estimate.** It comes from sets of 1 to 10 reps, with the Epley formula. It is
  not a tested one-rep max.
- **Current estimated max** is the best session in the last 28 days, or the latest session's when none
  is that recent.
- **A new best** beats every earlier set of that lift: a higher estimated max or a heavier weight; on a
  band, more reps; on an assisted lift, less help, then more reps at it. A hold's longer time counts at
  its weight, and a carry's heavier load at its distance. A lift's first session has nothing to beat, so
  it sets the mark and is never a new best; `log_lifts` says so in its summary.
- **Body weight now** is the 7-day average, or the latest weigh-in when the last 7 days have none.
  Goals and calorie estimates use it.
- **Trends and projections** are a straight line through the last 6 weeks. They need 4 points. They
  say where the line goes, not whether that pace is realistic.
- **Rounded figures:** the smoothed weight, the 7-day average and the estimated max come back at
  1 decimal place, wherever a result names them. They round whatever they were worked out from, so
  body weight now, the start of a body weight or lift goal and a cardio estimate's body weight round
  even when they come from a single weigh-in. A cardio entry's `estimate_weight_kg` is rounded with
  `estimate.weight_kg`, so the two read the same. A weigh-in's own `kg` stays as entered. A best of kind
  `estimated_max` and the one it beat carry 2 decimal places when 1 would read the same. A goal's
  `should_be_today` and `ahead_by` round at the goal unit's step: 1 decimal for kg and cm, whole bpm,
  minutes to 2 decimals. `ahead_by` takes one more decimal when latest and `should_be_today` would
  otherwise read the same, never for a cardio time. Every other figure keeps the precision it has.

Quote these numbers. Do not work out your own.

## Units

- **Kept in kg and km.** Every weight is kept in kg and every distance in km, at 2 decimal places. A
  result's number fields are in kg and km, as their names say.
- **The person's units** are kg or lb and km or mi, set with `update_settings` (`weight_unit`,
  `distance_unit`) or on Settings: kg and km until then. Switching changes no entry.
- **Shown in them:** every page, and every summary, text, label, plan_line and note. Claude says those
  figures and never converts a number field.
- **Sent as said.** A weight in pounds goes in `weight_lb` (a set) or `lb` (a weigh-in), a distance in
  miles in `distance_mi`, a speed in `speed_mph`, a goal's target with `weight_unit`. Spotter converts:
  lb x 0.45359237, mi x 1.609344, kept at 2 decimal places. A number said with no unit is in the
  person's units. A routine's weights from `get_routines` go in `weight_kg` as given.
- **Shown steps:** a set, a weigh-in and a goal's target to 0.05 lb; body weight now, the trend and an
  estimated max to 0.1 lb; a rate to 0.01 lb a week; miles to 0.01, mph to 0.1, a pace per mile as m:ss.
- **Asked once.** A weight in lb or a distance in miles, before the person has chosen, comes back with
  `units_note`: Claude asks once, and saves either answer.
- **Not offered:** stones, inches for a body size, a best mile (best times stay at 1, 5 and 10 km).

## Goals: four kinds

Every goal has a start, a target, a date and a direction, and comes back in the same shape from
`set_goal`, `get_goals` and `get_progress`.

| Kind | Target | Unit | "Now" | Direction |
|---|---|---|---|---|
| `lift` | An estimated one-rep max | kg | The best session in the last 28 days, else the latest session's | Up |
| `bodyweight` | A body weight | kg | Body weight now: the 7-day average, else the latest weigh-in | From the target against the start |
| `measurement` | waist, hips, chest, arm, thigh or resting_hr | cm or bpm | The latest value of that kind | From the target against the start |
| `cardio_best` | A time for a distance in one cardio activity | minutes | The best time for the distance in the last 28 days, else the latest | Down: faster |

- **A goal starts from "now"** on the day it is set. With nothing logged, the person's own start value
  is needed, in the goal's unit. A target equal to the start is refused.
- **A measurement goal** is a size or a resting heart rate to reach once. Steps, sleep hours and water
  are a day's amount, met every day, so they cannot be goals.
- **A cardio_best time** counts a session at or over the goal's distance. Its time for the goal
  distance is the session's pace times that distance: a 10 km run says something about a 5 km time;
  a 3 km run does not.
- **By the end of a month** means the last day of that month, the next one after today.
- **Each open goal** shows the pace it needs per week against the pace so far, and one status:
  reached, on track, behind, or not enough data.
- **The projected date** is the day the trend line reaches the target, at the pace so far. It is null
  when the line points away from the target, is flat, or reaches it more than 2 years from today.
- **The plan line** is a straight line from the goal's start to its target, by their dates. Each open
  goal says where that line is today (`should_be_today`), how far ahead or behind it the person is
  (`ahead_by`, and `days_ahead` in days), the word for it (`vs_plan`: ahead, on the line or behind),
  and one sentence to say as written (`plan_line`).
- **The plan line and the status answer different questions.** The status compares the trend's
  projection with the target, so a goal can be behind the plan line and still on track, or the reverse.

## Trends: how the person is doing

`get_trends` answers "how am I doing?". `get_goals` answers "am I on track?".

- **The smoothed weight trend** is an exponentially weighted moving average of the weigh-ins, with
  alpha 0.1: each weigh-in moves the line a tenth of the way from where it was. The chart on
  Progress draws this line. "Body weight now" stays the 7-day average.
- **The rate per week** comes from the smoothed line between two weigh-ins: the latest, dated in the
  last 7 days, and the last one dated 14 to 42 days before today. Without both ends the rate is null,
  and a note says which is missing.
- **Calories a day** are averaged over the days with food logged, in the last 7 and the last 28 days,
  with how much of it is Claude's estimates.
- **Sessions and cardio** are the last 7 days against the weekly targets, and the weekly average over
  the last 28 days.
- **The maintenance estimate** needs food logged on at least 14 of the last 21 days, and weigh-ins in
  the last 7 days and around 21 days ago. It is the average intake on the logged days, less the
  weight change at 7,700 kcal a kg spread over the 21 days. Until then a note says what it still
  needs. It is an estimate and it never sets the calorie target: what to do with it is the person's
  call.

## Food: who supplied each number

Every food entry carries one of four tags. Spotter works the tag out; Claude never names it.

| Tag | Who supplied the number | What Claude sends |
|---|---|---|
| `you_said` | The person stated it | `kcal`, with `estimated` false |
| `claude_estimate` | Claude worked it out | `kcal` with `estimated` true, always, and says so in the reply |
| `saved_food` | The app, from a saved food | the food's id and `servings`, no number |
| `usda` | The USDA table | the USDA id and `grams`, no number |

- A number never travels with an id: `kcal` with a saved or USDA food is refused with `invalid_field`.
- **The count is an amount.** For a saved or USDA food the count goes in `servings` or `grams`, never
  in the description. The description can be left out: it is then the food's name.
- When `find_food` finds no match and the person gave no number, Claude may estimate, and must say so.
- An estimate stays an estimate until the person states a number. Then Claude edits the entry's `kcal`
  and the tag becomes `you_said`.
- **The meal.** Claude passes the meal the person named for a food, and never asks. Otherwise Spotter
  picks it from the person's own clock: 04:00 to 11:00 breakfast, 11:00 to 16:00 lunch, 16:00 to
  22:00 dinner, otherwise snack (22:00 to 04:00, so half past midnight is a snack, not breakfast).
- **USDA.** When it is switched on, `find_food` also searches USDA FoodData Central, the United
  States' food composition data, with calories per 100 g. It covers foods as eaten in the US best.
  When it is off or does not answer, `find_food` says so.
- **No Indian food table values are shipped.** The Indian tables (IFCT 2017 and INDB) are not openly
  licensed, so their numbers cannot be included. Saved foods and the person's own numbers cover them.
- **The day's totals** show how much of the day's calories are estimates, and the target and what
  remains when a target is set. `net_kcal`, food minus cardio calories, appears only when the person
  has turned adding cardio calories back on; otherwise it is null.

## Cardio: the calorie estimate

- **The formula:** MET x 3.5 x body weight in kg / 200 x minutes, rounded to a whole number.
- **The body weight** is the person's body weight now: the 7-day average, or the latest weigh-in. With
  no weigh-in there is no estimate, and the result says why.
- **The machine's number** is kept beside the estimate, labelled as the machine's.
- **MET values** come from the 2024 Adult Compendium of Physical Activities, for the activities
  offered only. Calorie estimates use MET values from the 2024 Adult Compendium of Physical Activities
  (CC BY-NC-ND 4.0). The estimate names the Compendium code it used.

## What it cannot tell you

- It knows nothing about injury, pain, recovery, form or health.
- It gives no medical advice. A trend that looks bad is a number, not a diagnosis.
- It does not know why a session was light, missed or skipped.

## Duplicates, replacements, edits and deletes

- **Duplicates.** The same weight on the same day, the same measurement on the same day, the same
  set of foods or the same cardio session, logged again within 10 minutes comes back with
  `duplicate` true. Nothing new is written.
  Send `allow_repeat` true only when the person confirms it happened twice. Lifts are the exception:
  every `log_lifts` call adds its sets to that day's session, so a set said twice is logged twice.
  `restore_entry` with `undo_edit` takes back the last sets added.
  The same new goal set again within 10 minutes is not set twice: the first comes back with a warning.
- **Sending a write twice.** Any write takes an optional `key` of your own: 8 to 64 letters, digits,
  `-` or `_`. The same key with the same call again writes nothing and returns the first answer. The
  same key with a different call is refused with `key_reused`. A key whose first answer was not kept
  answers `already_done`: read the entry back before sending anything new. Keys are kept for a day.
  A new write takes a new key. With no key, the 10-minute rule above is the only guard, and lifts
  have none.
- **Replacements.** A second, different weigh-in on the same day replaces the first; so does a second
  value of the same measurement kind. The first is kept, not erased, and `restore_entry` brings it back.
- **Edits.** `edit_entry` changes an entry in place and keeps its id: a session's date, notes or whole
  set list, a weigh-in's weight or date, a measurement's value or date, a food entry's date, meal,
  description or kcal, a cardio entry's fields. Bests are worked out again from the whole history; a
  cardio estimate is worked out again; a food entry given a kcal becomes `you_said`. Moving a weigh-in or a measurement onto a day that already has one is refused, never
  silently replaced. An edit keeps the values from before it: `restore_entry` with `undo_edit`
  brings them back. The newest 10 are kept per entry, for 90 days. It undoes the last edit, once:
  sent again it changes nothing and comes back with `duplicate` true. To get an edit back, edit the
  entry again. A saved food that `save_food` changed is undone the same way, with kind
  `saved_food`. An undo that would move a weigh-in or a measurement onto a day that has another
  one is refused, as an edit is: nothing is replaced.
- **Deletes.** Nothing is ever erased. A delete takes two calls. `delete_entry` with no `confirm`
  deletes nothing: it answers with `dry_run` true, the entry as it stands and a `confirm` code.
  Claude shows the person the entry and asks, and sends the same kind and id with that `confirm`
  only when they say yes. A `confirm` that does not match the entry as it stands deletes nothing and
  answers with the preview again. `restore_entry` undoes a delete. Claude can delete unless the
  person has turned deleting off in Settings: then `delete_entry` answers `not_allowed`. When a
  person did not want an entry deleted, Claude restores it and says deleting can be turned off there.
- **The write limit.** An account takes at most 120 writes in any 10 minutes, from the web and Claude
  together. Every write call counts, a refused one too (`not_allowed`, `too_many_writes` or any other
  error). Past the limit, a write is refused with `too_many_writes` and the minutes to wait. The
  person's own permission switch is not limited.
- **The lookup limit.** `find_food` takes at most 120 lookups in any 10 minutes. Past it, a lookup is
  refused with `too_many_reads` and the minutes to wait.

## Importing from another app

`import_entries` brings history over from another app's export: weigh-ins, measurements, lift sessions,
food and cardio. Claude reads the export and sends it as dated rows, each with its kind and the named
fields of that kind. Every row it writes is marked `import` in `logged_by`, whoever sent it.

- **A preview first.** With `dry_run` true or left out, nothing is written. The result says what would
  be written, what is already there, what falls on a day Spotter already holds, and what was refused
  and why. Claude shows the person, and sends the same rows with `dry_run` false only when they say
  yes. `dry_run` is true, false or left out: any other value is refused.
- **Units.** Weights, distances, speeds and body sizes go as the export writes them, with `weight_unit`
  (kg or lb), `distance_unit` (km or mi) and `length_unit` (cm or in). Spotter converts them: lb x
  0.45359237, mi x 1.609344, in x 2.54, kept at 2 decimal places. A row's `speed` is in the call's
  distance unit an hour. The preview gives the lowest and highest numbers after conversion, so a wrong
  unit shows before anything is written.
- **A unit is never assumed.** A call whose rows hold a weight (a weigh-in's or a set's) needs
  `weight_unit`. A distance or a speed needs `distance_unit`. A body size (waist, hips, chest, arm or
  thigh) needs `length_unit`. A call that holds one of them without its unit is refused whole with
  `missing_unit`, a preview too, and nothing is written: the refusal names each missing unit. Rows that
  hold none of a quantity need no unit for it: food alone needs none, and so do resting heart rate,
  steps, sleep and water. A unit sent that no row needs is taken. The result's `units` shows only the
  units the call named.
- **Refused rows and refused calls.** A row the rules refuse fails alone, and the other rows go in. A
  call that does not fit the function's shape, such as a set with 2.5 reps, is refused whole before
  Spotter reads it: nothing is written, and it is not counted as a write.
- **Names.** A name not on the exercise list fails its row, and is never guessed. `unknown_names` gives
  each such name once, with the closest listed names Spotter found. They can be wrong.
  "Squat (Barbell)" is read as Back Squat.
- **Food** comes in as the other app's own numbers: tagged `you_said`, estimated false, and never among
  the recent entries `find_food` offers. Each entry needs its meal.
- **Cardio** gets its calorie estimate from body weight now, as every earlier-dated entry's does. With no
  weigh-in, imported cardio has no estimate.
- **Bests** are worked out from the whole history at read time: `get_progress` shows them after an import.

### How Claude sends an export

The server instructions carry only the guards: read the function's description first, preview first,
write only on a yes, every number as the export writes it with its unit named, and no name guessed. The
rest is in `import_entries`' own description:

- Rows go oldest first, with one day's rows of a kind in the same call, and the rest in the next call.
- Each row's timestamp goes with its offset when the export gives one, or its date. Rows more than 2
  years old are left out, and Claude says how many.
- When a name is not on the exercise list, Claude asks the person once per name which listed name it
  is, or whether to leave it out. It never leaves a lift out without saying so.
- From a phone's health store, one value a day: the day's total steps, the night's total sleep, the
  day's resting heart rate as the app gives it, and the day's first weigh-in.
- Each food entry goes with its meal as the export names it. When the export gives only a meal's or a
  day's total, it goes as one entry described as that total, such as Breakfast from the old app.
- For an import only, when the export gives only a day's total, Claude asks the person once which meal
  such totals go under. Logging food never asks which meal.
- A food entry's calories go only as the export gives them, never estimated for an import. A food entry
  with no calories is left out, and Claude tells the person how many.
- When the export does not say which unit it uses, Claude asks the person. One call names one unit for
  each quantity: rows in another unit go in a separate call.

### Dates

| `date` sent | Stored date |
|---|---|
| YYYY-MM-DD | That date |
| A timestamp with a non-zero offset, such as 2026-03-01T23:30:00+05:30 | Its own date as written: 1 March |
| A timestamp in UTC, ending Z or +00:00 | Turned into the person's time zone, then its date |
| A timestamp with no offset | Refused |
| Any other form of a date, such as 20261116 or 2026-W01-1 | Refused |

Then the usual rule: never after today, at most 2 years back. An import needs the person's time zone set.

### Caps

| Cap | Limit |
|---|---|
| Rows a call | 1 to 200 |
| Sets and food entries a call | 1,000 at most, together |
| Imported rows in any 24 hours | 10,000, deleted ones counted. A preview says whether the real run would pass. |
| The write limit | One call is one write of the 120, a preview too |

- The 1,000 sets and food entries run out before the 200 rows for food at six entries a day: 166 days
  is the most one call takes. They also run out first for lifting sent with one item a set and more
  than 5 sets a session.
- `times` on a set counts repeats: 3 sets of 5 at 100 kg is one item. A session holds at most 60 sets
  once `times` is counted.
- A call over a cap is refused whole, and nothing is written.

### Already there, and day taken

Each row is judged against what the person held before the call. Held means the person's entries on
the call's dates (live ones, and deleted ones that were imported), and the person's imported rows on any
date that an earlier import stored for this same row.

| Kind | Already there when | Day taken when, with no match, the day holds |
|---|---|---|
| Weigh-in | an entry has the date and the kg at 2 decimal places, or an earlier import stored it | a live weigh-in |
| Measurement | an entry has the date, the kind and the value, or an earlier import stored it | a live measurement of the same kind |
| Lift session | an earlier import stored it, or a session has the date and the same sets: exercise, set number, kg at 2 decimal places, reps. Notes do not count | a live session, held before the call |
| Food | per entry: an earlier import stored it, or an entry has the date, the meal, the description in any case and the whole kcal | a live food entry, held before the call. The matched entries are already there, the rest day taken |
| Cardio | an earlier import stored it, or an entry has the date, the activity, the minutes at 1 decimal place and the km at 2 | a live entry of the same activity, held before the call |

- A day-taken row is never added to and never replaces anything. The result names the entry that holds
  the day.
- Two identical sessions, food rows or cardio rows in one call both go in, and both match when the
  rows are sent again. `repeated_rows` counts them.
- Two weigh-ins on one day in one call, or two measurements of one kind: the first holds the day. A
  second with the same value is already there, and one with another value is day taken.

### Sending the same rows again

Each answer holds with the same time zone set at both runs. The last row is the exception.

| Between the runs, the person | The rows sent again |
|---|---|
| Changed nothing | Already there |
| Edited an imported row | Already there: the edit stands |
| Moved an imported row to another date | Already there: not written again on its first date |
| Deleted an imported row, edited or moved first or not | Already there: it stays deleted |
| Deleted rows imported in the wrong unit, then sent the file with the right unit | Written: the numbers differ |
| Logged a new entry by hand on an imported day | Day taken |
| Edited or deleted a row they logged by hand | Matched by its value on its date only. A deleted one is not held |
| Changed their time zone, and the rows carry timestamps in UTC | Can be written again, on another date: a known limit, below |

### Known limits

- Version 1 has no undo. An imported row is deleted like any other entry, one at a time, and
  `restore_entry` brings it back.
- A food entry imported beside entries the person had deleted is remembered by its own content and
  date, not by the whole day it came with. If the person moves it to another day and the same file is
  sent again, it comes back on its first day. If they edit it, delete it, and the same file is sent
  again, it comes back too.
- With the same time zone set, the same file brings back no other imported entry the person moved,
  edited or deleted. A food day imported whole is one of these.
- A row stamped in UTC, ending Z or +00:00, takes the person's own date in the time zone set when it is
  imported. If the person changes their time zone and the same rows are sent again, such a row can land
  on another date. It can then be written again there, beside the first copy. If that date already
  holds an entry of its kind, it is left out instead, day taken. A timestamp with its own
  offset, such as +05:30, keeps its own date, and so does a plain date: neither moves.
- If the person moves an imported food day's entries to another day, and a later export holds fewer
  entries for that first day, the entries it still holds are written again on the first day, unless
  that day holds a food entry. This is a different file, not the same rows sent again.
- What Claude reads does not mark an imported food entry as imported: no food result carries
  `logged_by`. The export does: `food_entries.csv` says `import`.

## Routines

- A routine is saved by name with `save_routine`: each lift with its number of sets and its reps, and a
  weight only when the person gave one. Up to 20 routines, each of 1 to 20 lifts and at most 60 sets,
  so a logged routine fits one session.
- `get_routines` gives each lift's weight to log: the heaviest set of its latest session, unless the
  routine was saved on a later day than that session, with a weight of its own. With neither, Claude
  asks.
- To log one, Claude sends its sets with `log_lifts`, with each change the person names. They go into
  that day's session, as any sets do.
- Saving the same name again replaces the routine. There is no undo of a replace: save it again as it
  was. `delete_entry` takes kind `routine`, in its two calls, and `restore_entry` brings a deleted
  routine back.
- The import does not bring routines over, and the export holds them in `routines.csv`.

## Data on request

- **Two reads.** `what_is_logged` names every area, its fields, what each means and the totals each
  takes, with how much the person has logged. `get_data` reads one area: rows, or groups with totals.
- **Areas:** lift sets, lift sessions, cardio, weigh-ins, measurements, food, saved foods, and days, one
  row a day across them. Goals and routines stay with `get_goals` and `get_routines`.
- **Totals** are Spotter's: sum, avg, max, min, count, per_day and share. per_day divides by the days
  with an entry in the area, or for a measurement by the days with that kind. A share is a whole percent,
  and the shares sum to 100. A pace over a range is total minutes over total distance.
- **Ranges.** Left out, the last 28 days. Weeks run Monday to Sunday and months are calendar months.
- **Size.** 100 rows by default, at most 300, and an answer of at most 30,000 characters. `more` gives
  the next rows.
- **Units.** Every value is in the person's units, and each column's name says which.
- **Body region** of a lift (upper, lower, core or full body) comes from its main muscles in
  free-exercise-db, save every carry and sled, which are full body.
- **Muscles.** Each lift's muscles are free-exercise-db's. Dead Hang's are Spotter's own: forearms and
  lats. Grouped by muscle, a set counts 1 for its main muscle and 0.5 for each other muscle it works.
- **Volume** is weight x reps, x 2 for a pair of weights (dumbbells, kettlebells or two cables), x 2 for
  one side at a time. A hold or a carry has none. A set at 0 kg or below, help among them, counts 0.
- **Not offered:** joins across areas in one call, deleted entries, raw queries.

## Export and delete: the person's own clicks

- Exporting the data (a zip of CSV files) and deleting the account are the person's own clicks on
  Settings. No function does either. Claude never offers to do them, and says they are on Settings.
- Deleting the account removes the sign-in and every row of the person's. It cannot be undone. The
  call log keeps its counts, with no person on them.
- A sign-in already given to Claude stays valid until it expires, an hour at most. A call in that
  hour recreates an empty account for the deleted sign-in. It holds nothing, and no later sign-in
  reaches it.

## What "today" means

- Today is the person's own date, in their time zone. Leave the date out to log something done today.
- Until a time zone is set, today is the UTC date. For someone far from UTC, that can be the wrong day
  in their morning or evening.
- Claude sets the time zone with `update_settings`, the first time `whoami` carries `time_zone_note`,
  before logging anything. It uses the person's zone if it knows it, and otherwise asks once which city
  they are in. The zone is an IANA name such as Asia/Kolkata; an abbreviation or an offset is refused.
- A date is sent as YYYY-MM-DD. No entry can be dated after today, or more than 2 years back. A goal's
  target date is after today, and at most 5 years on.
- Changing the time zone changes no entry: each keeps the date it was logged with. One that is then
  dated after today still shows, and counts as today's in every number above.

## What a call can send

- **Only the fields a function lists.** Any other field is refused with `invalid_field`, and nothing is
  written. Its fix lists the fields the function takes, and names the nearest one when there is one.
- **Numbers as numbers.** true and false are never a number. Text that holds a plain number, such as
  "80", is read as that number. Other text, such as 1_0, 8e1 or digits of another script, is refused.
- **A refused call writes nothing.** Its fix says what to send instead.

## Feedback

`send_feedback` sends a note to Spotter's maker, the person who runs Spotter: a problem, something
confusing, an idea.

- Claude sends it only when the person asks, or says yes to an offer, in the person's own words.
- When the person complains about Spotter, or something in it keeps going wrong, Claude offers once to
  send it as feedback, and sends it only on a yes.
- Before sending, Claude asks once for what is missing of three parts: what they were doing, what they
  expected, what happened.
- It is a write, allowed at both settings.
- The number of notes in any 24 hours is capped: `send_feedback`'s description gives the number. Past
  it, a note is refused with `too_much_feedback` and the hours to wait.
- The same note again within 10 minutes is saved once: it comes back with `duplicate` true. There is
  no `allow_repeat` for a note.
- Spotter's maker reads each note with the names of the functions the account used around that time,
  never what was logged.
- No reply comes back through Spotter.
- A note is kept until Spotter's maker deletes it, one year at most. Until then it is in the export,
  and deleting the account deletes it.

## A new person

- While nothing is logged, `whoami` carries `new_account_note`: no live entry, no saved food and no open goal.
- Claude then gives the person `links.hi_there` once, last in the reply.
- The link is a short read: what to say, what Spotter keeps and how to bring history over. It has nothing to fill in.
- The note goes after the first entry, saved food or open goal. It comes back if the account is emptied again.
- `links.hi_there` is always there. Give it when the person asks what Spotter can do.

## Setting up

- While a setup step other than the import is not done, `whoami` carries `setup`: `missing`, the steps not done yet in order, and `how`.
- A step is done when the account holds it: a time zone, a live weigh-in, a live lift session, an open goal, each target set, a live saved food, a live routine, a live imported row.
- Claude walks the steps only when the person asks to set up, or once on a new account. One question at a time. Any step can be skipped, and comes back the next time the person asks.

## What Claude may do

Claude may call these functions, and nothing else:

| Function | What it does |
|---|---|
| `whoami` | Says whose account this is, today's date for them, and what Claude may do. Call it first. |
| `list_exercises` | The exercise names, and a search. |
| `log` | Records one entry of a kind: lifts, bodyweight, measurement, food or cardio. |
| `guide` | Says how to log a kind: what its parts take, and its fields with their units and rules. |
| `set_goal` | Sets a goal of any of the four kinds, or changes or closes one. |
| `get_progress` | Reads progress back: one lift over time, or every lift. |
| `update_settings` | Sets the person's time zone, units and targets. |
| `get_log` | What the person logged, day by day, for 1 to 14 days. |
| `edit_entry` | Changes a session, a weigh-in, a measurement, a food entry or a cardio entry in place. |
| `restore_entry` | Brings back a deleted or replaced entry, or undoes an entry's last edit. |
| `delete_entry` | Deletes an entry in two calls: a preview, then the delete once the person says yes. |
| `find_food` | Looks a food up: saved foods and recent entries first, then USDA when it is on. |
| `save_food` | Saves a food with its serving and calories, or updates one. |
| `get_trends` | How the person is doing: the weight trend and its rate, calories, sessions, cardio, maintenance. |
| `get_goals` | Whether the person is on track: every open goal, its pace and its projected date. |
| `import_entries` | Brings history over from another app's export, as dated rows: a preview first, then the rows. |
| `send_feedback` | Sends a note to Spotter's maker, when the person asks or says yes to the offer. |
| `save_routine` | Saves a workout by name, with its lifts, sets and reps. |
| `get_routines` | The saved routines, each lift with the weight to log. |
| `what_is_logged` | What Spotter can hold and what the person has logged, area by area, with the open goals. |
| `get_data` | One query over the person's entries: an area, its fields, filters, groups and totals, all worked out by Spotter. |

A chat opened before the change holds log_lifts, log_bodyweight, log_measurement, log_food and log_cardio in their place: they log the same entries.

`whoami` reports `claude_can`, the person's setting for what Claude may do:

| Setting | Claude may |
|---|---|
| `log_delete` (the default) | Everything, `delete_entry` too. |
| `log` | Everything except `delete_entry`. |

With `log`, `delete_entry` answers `not_allowed`, and nothing is deleted. Claude cannot change the
setting: only the person can.
