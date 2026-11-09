# What Spotter can and cannot tell you

Spotter is one person's own record of their training. It stores what they log and works out numbers
from it. It knows nothing else.

## What it records today

- **Lift sessions:** the date, the sets (exercise, weight in kg, reps) and optional notes.
- **Body weight:** one weigh-in a day, in kg.
- **Goals:** a lift to reach (an estimated one-rep max) or a body weight to reach, by a date.

Exercise names come from one shared list. Use them exactly as `list_exercises` gives them.

## What its numbers are

- **Logged facts:** sets, weights, reps, weigh-ins and dates are what the person logged. Nothing more.
- **Estimated max is an estimate.** It comes from sets of 1 to 10 reps, with the Epley formula. It is
  not a tested one-rep max.
- **Current estimated max** is the best session in the last 28 days, or the latest session's when none
  is that recent.
- **Body weight now** is the 7-day average, or the latest weigh-in when the last 7 days have none.
- **Trends and projections** are a straight line through the last 6 weeks. They need 4 points. They
  say where the line goes, not whether that pace is realistic.

Quote these numbers. Do not work out your own.

## What it cannot tell you

- It knows nothing about injury, pain, sleep, recovery, form or health.
- It gives no medical advice. A trend that looks bad is a number, not a diagnosis.
- It does not know why a session was light, missed or skipped.

## Duplicates, replaced weigh-ins and deletes

- **Duplicates.** The same session, or the same weight on the same day, logged again within
  10 minutes comes back with `duplicate` true. Nothing new is written. Send `allow_repeat` true only
  when the person confirms it really happened twice.
- **Replaced weigh-ins.** A second, different weigh-in on the same day replaces the first. The first is
  kept, not erased, and can be restored later. Claude cannot restore it in this stage.
- **Deletes.** Nothing is ever erased. Claude cannot delete anything in this stage.

## What "today" means

- Today is the person's own date, in their time zone. Leave the date out to log something done today.
- Until a time zone is set, today is the UTC date. For someone far from UTC, that can be the wrong day
  in their morning or evening. When `whoami` carries `time_zone_note`, ask the person their time zone
  and send them to the Settings page to set it.
- No entry can be dated after today, or more than 2 years back.

## What Claude may do

Six functions, nothing else:

| Function | What it does |
|---|---|
| `whoami` | Says whose account this is, today's date for them, and what Claude may do. Call it first. |
| `list_exercises` | The exercise names to use. |
| `log_lifts` | Records one lift session. |
| `log_bodyweight` | Records one weigh-in. |
| `set_goal` | Sets a lift or body weight goal, or changes or closes one. |
| `get_progress` | Reads progress back: one lift over time, or every lift. |

`whoami` reports `claude_can`, the person's setting for what Claude may do. In this stage Claude can
read and log, and cannot delete, whatever that setting says.
