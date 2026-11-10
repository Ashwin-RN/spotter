# What Spotter can and cannot tell you

Spotter is one person's own record of their training. It stores what they log and works out numbers
from it. It knows nothing else.

## What it records today

- **Lift sessions:** the date, the sets (exercise, weight in kg, reps) and optional notes.
- **Body weight:** one weigh-in a day, in kg.
- **Measurements:** nine kinds, each with its own unit: waist, hips, chest, arm and thigh in cm,
  steps, sleep_hours in hours, resting_hr in bpm, and water_ml in ml. One value per kind a day.
- **Food:** what was eaten, its calories, its meal, and a tag saying who supplied the number.
- **Saved foods:** foods the person eats often, with a serving and its calories.
- **Cardio:** the activity, minutes, and distance, incline, speed, heart rate and the machine's
  calories when given.
- **Goals:** a lift to reach (an estimated one-rep max) or a body weight to reach, by a date.

Exercise and cardio activity names come from one shared list. Use them exactly as `list_exercises`
gives them.

## What its numbers are

- **Logged facts:** sets, weights, reps, weigh-ins and dates are what the person logged. Nothing more.
- **Estimated max is an estimate.** It comes from sets of 1 to 10 reps, with the Epley formula. It is
  not a tested one-rep max.
- **Current estimated max** is the best session in the last 28 days, or the latest session's when none
  is that recent.
- **A new best** beats every earlier set of that lift. A lift's first session has nothing to beat, so
  it sets the mark and is never a new best; `log_lifts` says so in its summary.
- **Body weight now** is the 7-day average, or the latest weigh-in when the last 7 days have none.
- **Trends and projections** are a straight line through the last 6 weeks. They need 4 points. They
  say where the line goes, not whether that pace is realistic.

Quote these numbers. Do not work out your own.

## Food: who supplied each number

Every food entry carries one of four tags. Spotter works the tag out; Claude never names it.

| Tag | Who supplied the number | What Claude sends |
|---|---|---|
| `you_said` | The person stated it | `kcal`, with `estimated` false |
| `claude_estimate` | Claude worked it out | `kcal` with `estimated` true, always, and says so in the reply |
| `saved_food` | The app, from a saved food | the food's id and `servings`, no number |
| `usda` | The USDA table | the USDA id and `grams`, no number |

- A number never travels with an id: `kcal` with a saved or USDA food is refused with `invalid_field`.
- When `find_food` finds no match and the person gave no number, Claude may estimate, and must say so.
- An estimate stays an estimate until the person states a number. Then Claude edits the entry's `kcal`
  and the tag becomes `you_said`.
- **The meal.** Claude passes a meal only when the person named one, and never asks. Otherwise Spotter
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

- It knows nothing about injury, pain, sleep, recovery, form or health.
- It gives no medical advice. A trend that looks bad is a number, not a diagnosis.
- It does not know why a session was light, missed or skipped.

## Duplicates, replacements, edits and deletes

- **Duplicates.** The same session, the same weight on the same day, the same measurement on the
  same day, the same set of foods or the same cardio session, logged again within 10 minutes comes
  back with `duplicate` true. Nothing new is written.
  Send `allow_repeat` true only when the person confirms it really happened twice.
- **Replacements.** A second, different weigh-in on the same day replaces the first; so does a second
  value of the same measurement kind. The first is kept, not erased, and `restore_entry` brings it back.
- **Edits.** `edit_entry` changes an entry in place and keeps its id: a session's date, notes or whole
  set list, a weigh-in's kg or date, a measurement's value or date, a food entry's date, meal,
  description or kcal, a cardio entry's fields. Bests are worked out again from the whole history; a
  cardio estimate is worked out again; a food entry given a kcal becomes `you_said`. Moving a weigh-in or a measurement onto a day that already has one is refused, never
  silently replaced. An edit keeps no old values: it cannot be undone, only edited again.
- **Deletes.** Nothing is ever erased. Claude can delete only when the person has allowed it in
  Settings, and `restore_entry` undoes a delete.
- **The write limit.** An account takes at most 120 writes in any 10 minutes, from the web and Claude
  together. Every write call counts, a refused one too (`not_allowed`, `too_many_writes` or any other
  error). Past the limit, a write is refused with `too_many_writes` and the minutes to wait. The
  person's own permission switch is not limited.

## What "today" means

- Today is the person's own date, in their time zone. Leave the date out to log something done today.
- Until a time zone is set, today is the UTC date. For someone far from UTC, that can be the wrong day
  in their morning or evening.
- Claude sets the time zone with `update_settings`, the first time `whoami` carries `time_zone_note`,
  before logging anything. It uses the person's zone if it knows it, and otherwise asks once which city
  they are in. The zone is an IANA name such as Asia/Kolkata; an abbreviation or an offset is refused.
- No entry can be dated after today, or more than 2 years back.
- Changing the time zone changes no entry: each keeps the date it was logged with. One that is then
  dated after today still shows, and counts as today's in every number above.

## What Claude may do

Sixteen functions, nothing else:

| Function | What it does |
|---|---|
| `whoami` | Says whose account this is, today's date for them, and what Claude may do. Call it first. |
| `list_exercises` | The exercise names to use. |
| `log_lifts` | Records one lift session. |
| `log_bodyweight` | Records one weigh-in. |
| `set_goal` | Sets a lift or body weight goal, or changes or closes one. |
| `get_progress` | Reads progress back: one lift over time, or every lift. |
| `update_settings` | Sets the person's time zone and targets. |
| `get_log` | What the person logged, day by day, for 1 to 14 days. |
| `log_measurement` | Records one measurement. |
| `edit_entry` | Changes a session, a weigh-in, a measurement, a food entry or a cardio entry in place. |
| `restore_entry` | Brings back a deleted or replaced entry. |
| `delete_entry` | Deletes an entry, when the person allows it. |
| `find_food` | Looks a food up: saved foods and recent entries first, then USDA when it is on. |
| `log_food` | Records what the person ate, every food in one call. |
| `save_food` | Saves a food with its serving and calories, or updates one. |
| `log_cardio` | Records one cardio session, with a calorie estimate. |

`whoami` reports `claude_can`, the person's setting for what Claude may do:

| Setting | Claude may |
|---|---|
| `read` | Read only: `whoami`, `list_exercises`, `get_progress`, `get_log`, `find_food`. |
| `log` (the default) | Read, and every write except `delete_entry`. |
| `log_delete` | Everything, `delete_entry` too. |

A function the setting does not allow answers `not_allowed`, and nothing is written. Claude cannot
change the setting: only the person can.
