# What Spotter can and cannot tell you

Spotter is one person's own record of their training. It stores what they log and works out numbers
from it. It knows nothing else.

## What it records today

- **Lift sessions:** the date, the sets (exercise, weight in kg, reps) and optional notes.
- **Body weight:** one weigh-in a day, in kg.
- **Measurements:** nine kinds, each with its own unit: waist, hips, chest, arm and thigh in cm,
  steps, sleep_hours in hours, resting_hr in bpm, and water_ml in ml. One value per kind a day.
- **Goals:** a lift to reach (an estimated one-rep max) or a body weight to reach, by a date.

Exercise names come from one shared list. Use them exactly as `list_exercises` gives them.

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

## What it cannot tell you

- It knows nothing about injury, pain, sleep, recovery, form or health.
- It gives no medical advice. A trend that looks bad is a number, not a diagnosis.
- It does not know why a session was light, missed or skipped.

## Duplicates, replacements, edits and deletes

- **Duplicates.** The same session, the same weight on the same day, or the same measurement on the
  same day, logged again within 10 minutes comes back with `duplicate` true. Nothing new is written.
  Send `allow_repeat` true only when the person confirms it really happened twice.
- **Replacements.** A second, different weigh-in on the same day replaces the first; so does a second
  value of the same measurement kind. The first is kept, not erased, and `restore_entry` brings it back.
- **Edits.** `edit_entry` changes an entry in place and keeps its id: a session's date, notes or whole
  set list, a weigh-in's kg or date, a measurement's value or date. Bests are worked out again from the
  whole history. Moving a weigh-in or a measurement onto a day that already has one is refused, never
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

Twelve functions, nothing else:

| Function | What it does |
|---|---|
| `whoami` | Says whose account this is, today's date for them, and what Claude may do. Call it first. |
| `list_exercises` | The exercise names to use. |
| `log_lifts` | Records one lift session. |
| `log_bodyweight` | Records one weigh-in. |
| `set_goal` | Sets a lift or body weight goal, or changes or closes one. |
| `get_progress` | Reads progress back: one lift over time, or every lift. |
| `update_settings` | Sets the person's time zone. |
| `get_log` | What the person logged, day by day, for 1 to 14 days. |
| `log_measurement` | Records one measurement. |
| `edit_entry` | Changes a session, a weigh-in or a measurement in place. |
| `restore_entry` | Brings back a deleted or replaced entry. |
| `delete_entry` | Deletes an entry, when the person allows it. |

`whoami` reports `claude_can`, the person's setting for what Claude may do:

| Setting | Claude may |
|---|---|
| `read` | Read only: `whoami`, `list_exercises`, `get_progress`, `get_log`. |
| `log` (the default) | Read, and every write except `delete_entry`. |
| `log_delete` | Everything, `delete_entry` too. |

A function the setting does not allow answers `not_allowed`, and nothing is written. Claude cannot
change the setting: only the person can.
