"""Programs: the person's training program, a weekday schedule in blocks of weeks (contract routines-and-programs.md,
decisions 15 to 20, R8 to R12, and its note of 2026-11-23, which overrules the rows above it).

save_program(user_id, data, by)
    data: name (1 to 64 characters), start_on (YYYY-MM-DD; blank or left out, see below), blocks (the weeks in order:
    each {weeks, days}, days 1 to 7 of {weekday, routine}). Every failure at once (rules.combine), nothing written:
    - name: as a routine's, checked as sent, stripped, then stored with its spaces folded.
    - start_on blank or left out: the live program's stored start while that program is not started or running
      today (by its stored start and weeks), so a change to it keeps its week (the owner's pick of 2026-11-23, the
      review's S1); today when there is no program, when it is deleted, or when it has ended. The stored start is
      kept as it is, not checked again.
    - start_on sent: not a date is invalid_date; more than two years back (rules.OLDEST_DAYS) date_too_old; more than 52
      weeks after today (rules.PROGRAM_WEEKS, the cap) date_too_far, goals' code. Each names start_on in field.
      rules.entry_date refuses every date after today, so a start is checked here.
    - blocks: 1 to 52 of them (each holds a week at least); each block's weeks a whole number of 1 or more, and the
      weeks in all 1 to 52 (the cap); 1 to 7 days a block, each weekday at most once; a weekday is monday to sunday,
      case and spaces forgiven; a day's routine a live routine's name, matched by its name key as save_routine folds
      it (spaces folded, casefolded), and stored as the routine's own stored name. A name that is not live is
      unknown_routine, with the live names.
    One row a person, deleted or not (uq_program_user_id). None makes one (created true). One is replaced, its name
    too, and brought back if deleted. Blocks are stored as [{"weeks": n, "days": [{"weekday": "monday", "routine":
    "Push day"}]}], days in weekday order. A live program whose name, start and blocks are what is stored writes
    nothing: updated_at stays, and the answer says it is already saved. No version is kept: nothing undoes a replace.
    Returns {kind: "program", id, created, program {id, name, start_on, blocks, weeks, updated_at}, summary, links
    {routines}} or {error, message, fix}. The write locks the user row first (common.lock_user), then checks the
    write limit (common.check_write_limit), before any other read or work.

live_program(s, user_id)
    The person's live program row, or None: one statement, by user_id. get_routines runs it on every read.

program_read(row, today, live_keys)
    The program as get_routines gives it (R10, the note's states): {id, name, start_on, weeks, week, state, today,
    next, this_week, blocks, words}. A program week starts on start_on's weekday; week n runs from start_on + 7 (n - 1)
    days, and its days are its block's. Running (start_on <= today < start_on + 7 weeks): week, today (null on a rest
    day), next (the first later day with a routine, {date, routine}, or null when none is left) and this_week (the 7
    days of the program week, {date, weekday, routine}). Not started: week, today and this_week null, next the first
    day with a routine. Ended: week, today, this_week and next null. A day whose routine is not live (live_keys: the
    live routines' name keys) keeps its stored name, and words says "Pull day is not saved now." for a routine words
    names (today's, the next or the first).

program_words(row, today)
    The program in one line, for a delete's preview: "PPL: 8 weeks from Mon 23 Nov. Push day on Monday, Pull day on
    Wednesday and Legs day on Friday."
"""

import json
from datetime import timedelta

from sqlalchemy import select

from app import db, formatting, links, rules, users
from app.models import Program, Routine
from app.services.common import check_by, check_data, check_write_limit, lock_user, no_account
from app.services.lifts import _joined  # the one join of a list in words

KIND = "program"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")  # date.weekday()'s order
FIELDS = ("name", "start_on", "blocks")
BLOCK_FIELDS = ("weeks", "days")
DAY_FIELDS = ("weekday", "routine")
DAYS_A_BLOCK = (1, 7)  # a week's days: each weekday at most once


def name_key(name: str) -> str:
    """A routine's name key, as save_routine folds it: spaces folded, casefolded."""
    return " ".join(name.split()).casefold()


def _stored(row: Program) -> list:
    return json.loads(row.blocks)


def total_weeks(blocks: list) -> int:
    return sum(block["weeks"] for block in blocks)


def program_dict(row: Program) -> dict:
    blocks = _stored(row)
    return {"id": row.id, "name": row.name, "start_on": row.start_on.isoformat(), "blocks": blocks,
            "weeks": total_weeks(blocks), "updated_at": row.updated_at.isoformat()}


def _weeks_text(n: int) -> str:
    return f"{formatting.show(n, 'count')} week{'' if n == 1 else 's'}"


def _days_text(days: list) -> str:
    """'Push day on Monday, Pull day on Wednesday and Legs day on Friday'."""
    return _joined([f"{d['routine']} on {d['weekday'].capitalize()}" for d in days])


def schedule_text(blocks: list, start_on, today) -> str:
    """'8 weeks from Mon 23 Nov. Push day on Monday, Pull day on Wednesday and Legs day on Friday.' Several blocks:
    '8 weeks from Mon 23 Nov. Weeks 1 to 4: ... Weeks 5 to 8: ...', a block of one week 'Week 5: ...'."""
    head = f"{_weeks_text(total_weeks(blocks))} from {formatting.day(start_on, today)}."
    if len(blocks) == 1:
        return f"{head} {_days_text(blocks[0]['days'])}."
    parts, first = [head], 1
    for block in blocks:
        last = first + block["weeks"] - 1
        span = f"Week {first}" if first == last else f"Weeks {first} to {last}"
        parts.append(f"{span}: {_days_text(block['days'])}.")
        first = last + 1
    return " ".join(parts)


def program_words(row: Program, today) -> str:
    """The program in one line, for a delete's preview."""
    return f"{row.name}: {schedule_text(_stored(row), row.start_on, today)}"


# ---------------------------------------------------------------- the checks

def _kept_start(row: Program | None, today):
    """The start a blank start_on keeps: the live program's while it is not started or running today, else None."""
    if row is None or row.deleted_at is not None:
        return None
    return row.start_on if today < row.start_on + timedelta(weeks=total_weeks(_stored(row))) else None


def _start(value, today, kept=None) -> tuple:
    """start_on: blank or left out is kept, the live program's start (_kept_start), or today when there is none
    (decision 18, the note of 2026-11-23, the owner's pick of 2026-11-23)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return kept or today, None
    parsed = rules.to_date(value)
    if parsed is None:
        return None, rules.error("invalid_date", f"start_on '{rules.quote(value)}' is not a date.",
                                 "Give the date as YYYY-MM-DD, or leave it out for today.", field="start_on")
    if parsed < today - timedelta(days=rules.OLDEST_DAYS):
        return None, rules.error("date_too_old", f"start_on {parsed.isoformat()} is more than 2 years ago.",
                                 "Give a start from the last 2 years only; check the year.", field="start_on")
    weeks = rules.PROGRAM_WEEKS[1]
    latest = today + timedelta(weeks=weeks)
    if parsed > latest:
        return None, rules.error("date_too_far", f"start_on {parsed.isoformat()} is more than {weeks} weeks after "
                                 f"today, {today.isoformat()}.",
                                 f"Give a start at most {weeks} weeks on: {latest.isoformat()} at the latest.",
                                 field="start_on")
    return parsed, None


def _shown(value) -> str:
    return f"'{rules.quote(value)}'" if isinstance(value, str) else rules.as_sent(value)


def _weekday(value, where: str) -> tuple:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, rules.error("missing_field", f"{where}: weekday is missing.", "Use monday to sunday.")
    key = " ".join(value.split()).casefold() if isinstance(value, str) else None
    if key not in WEEKDAYS:
        return None, rules.error("invalid_field", f"{where}: {_shown(value)} is not a weekday.",
                                 "Use monday to sunday.")
    return key, None


def _routine(value, where: str, live: list) -> tuple:
    """(the routine's stored name, None) or (None, the refusal). live: the live routines' (name, key), in
    get_routines' order."""
    if not isinstance(value, str) or not value.strip():
        return None, rules.error("missing_field", f"{where}: routine is missing.",
                                 "Give a saved routine's name, as get_routines gives it.")
    stored = {key: name for name, key in live}.get(name_key(value))
    if stored is not None:
        return stored, None
    said = f"{where}: '{rules.quote(value.strip())}' is not a saved routine."
    if not live:
        return None, rules.error("unknown_routine", f"{said} No routine is saved yet.",
                                 "Save the routine first with save_routine.")
    return None, rules.error("unknown_routine", f"{said} Saved: {_joined([n for n, _ in live])}.",
                             "Send a name as get_routines gives it, or save the routine first with save_routine.")


def _days(raw, n: int, live: list) -> tuple:
    """(a block's days in weekday order, every failure)."""
    where = f"Block {n}"
    if raw is None:
        return [], [rules.error("missing_field", f"{where}: days is missing.",
                                f"Give the days of block {n}, each a weekday and a routine.")]
    lo, hi = DAYS_A_BLOCK
    sent_days = raw if isinstance(raw, list) else []
    errors = []
    if not isinstance(raw, list) or not lo <= len(sent_days) <= hi:
        count = formatting.show(len(sent_days), "count")
        errors.append(rules.error("invalid_field", f"{where} holds {lo} to {hi} days, not {count}.",
                                  f"Send {lo} to {hi} days in block {n}, each weekday once."))
    days, seen = [], {}
    for m, day in enumerate(sent_days[:hi], start=1):
        at = f"{where}, day {m}"
        if not isinstance(day, dict):
            errors.append(rules.error("invalid_field", f"{at}: it must hold a weekday and a routine.",
                                      "Send each day as weekday and routine."))
            continue
        errors += [rules.error("invalid_field", f"{at}: '{rules.quote(k)}' is not a field of a day.",
                               f"Use: {', '.join(DAY_FIELDS)}.") for k in day if k not in DAY_FIELDS]
        weekday, err = _weekday(day.get("weekday"), at)
        errors += [err] if err else []
        if weekday is not None:
            seen[weekday] = seen.get(weekday, 0) + 1
        routine, err = _routine(day.get("routine"), f"{where}, {weekday.capitalize()}" if weekday else at, live)
        errors += [err] if err else []
        if weekday is not None and routine is not None:
            days.append({"weekday": weekday, "routine": routine})
    errors += [rules.error("invalid_field", f"{where}: {weekday} is there twice.", "Give each weekday once in a block.")
               for weekday, count in seen.items() if count > 1]
    return sorted(days, key=lambda d: WEEKDAYS.index(d["weekday"])), errors


def _blocks(raw, live: list) -> tuple:
    """(the blocks as stored, every failure)."""
    lo, hi = rules.PROGRAM_WEEKS
    if raw is None:
        return [], [rules.error("missing_field", "blocks is missing.", "Give blocks, each with its weeks and days.")]
    sent_blocks = raw if isinstance(raw, list) else []
    errors = []
    if not isinstance(raw, list) or not lo <= len(sent_blocks) <= hi:  # a block holds a week at least
        errors.append(rules.error("invalid_field",
                                  f"A program holds {lo} to {hi} blocks, not "
                                  f"{formatting.show(len(sent_blocks), 'count')}.",
                                  f"Send {lo} to {hi} blocks, each with its weeks and days."))
    blocks, total = [], 0
    for n, block in enumerate(sent_blocks[:hi], start=1):
        where = f"Block {n}"
        if not isinstance(block, dict):
            errors.append(rules.error("invalid_field", f"{where}: it must hold weeks and days.",
                                      f"Send block {n} as weeks and days."))
            continue
        errors += [rules.error("invalid_field", f"{where}: '{rules.quote(k)}' is not a field of a block.",
                               f"Use: {', '.join(BLOCK_FIELDS)}.") for k in block if k not in BLOCK_FIELDS]
        weeks, err = rules.number(block.get("weeks"), int, "weeks", where)
        if err is None and weeks < lo:
            err = rules.error("invalid_field", f"{where}: weeks {rules.as_sent(block.get('weeks'))} is less than {lo}.",
                              f"Give weeks on block {n} as {lo} or more.")
        errors += [err] if err else []
        total += weeks if err is None else 0
        days, found = _days(block.get("days"), n, live)
        errors += found
        if err is None:
            blocks.append({"weeks": weeks, "days": days})
    if total > hi:
        errors.append(rules.error("invalid_field", f"A program runs {lo} to {hi} weeks, not "
                                                   f"{formatting.show(total, 'count')}.", "Send fewer weeks."))
    return blocks, errors


def _checked(data: dict, today, live: list, kept=None) -> tuple:
    """(the name, the start, the blocks, every failure). kept: the start a blank start_on keeps, or None."""
    name, err = rules.required_text(data.get("name"), "name", limit=rules.ROUTINE_NAME_MAX)  # a routine's limit
    name = " ".join(name.split()) if name else name
    errors = [err] if err else []
    errors += [rules.error("invalid_field", f"'{rules.quote(k)}' is not a field of a program.",
                           f"Use: {', '.join(FIELDS)}.") for k in data if k not in FIELDS]
    start, err = _start(data.get("start_on"), today, kept)
    errors += [err] if err else []
    blocks, found = _blocks(data.get("blocks"), live)
    return name, start, blocks, errors + found


def live_routines(s, user_id: int) -> list:
    """The live routines' (name, name_key), in get_routines' order."""
    return [(name, key) for name, key in s.execute(select(Routine.name, Routine.name_key)
                                                   .where(Routine.user_id == user_id, Routine.deleted_at.is_(None))
                                                   .order_by(Routine.name_key, Routine.id))]


def save_program(user_id: int, data: dict | None, by: str) -> dict:
    """Save the person's program, or replace the one there. See the module notes."""
    data, err = check_data(data)
    err = err or check_by(by)
    if err:
        return err
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        today = users.today_for(user)
        row = s.scalar(select(Program).where(Program.user_id == user.id))
        name, start, blocks, errors = _checked(data, today, live_routines(s, user.id), _kept_start(row, today))
        if errors:
            return rules.combine(errors)
        created = row is None
        was_deleted = not created and row.deleted_at is not None
        said = f"{name}: {schedule_text(blocks, start, today)}"
        if not (created or was_deleted) and (row.name, row.start_on, _stored(row)) == (name, start, blocks):
            summary = f"{name} is already saved as given: nothing changed."
        else:
            if created:
                row = Program(user_id=user.id)
                s.add(row)
            row.name, row.start_on, row.blocks = name, start, json.dumps(blocks, separators=(",", ":"))
            row.deleted_at, row.updated_at = None, db.utcnow()
            s.flush()
            summary = (f"Saved {said}" if created else
                       f"Brought back and replaced {said}" if was_deleted else f"Replaced your program with {said}")
        return {"kind": KIND, "id": row.id, "created": created, "program": program_dict(row), "summary": summary,
                "links": {"routines": links.page("routines")}}


# ---------------------------------------------------------------- the read

def live_program(s, user_id: int) -> Program | None:
    return s.scalar(select(Program).where(Program.user_id == user_id, Program.deleted_at.is_(None)))


def _routine_on(blocks: list, start_on, d) -> str | None:
    """The routine for day d of the program, or None for a rest day or a day outside it."""
    week = (d - start_on).days // 7 + 1
    if week < 1:
        return None
    first = 1
    for block in blocks:
        if week < first + block["weeks"]:
            return next((x["routine"] for x in block["days"] if x["weekday"] == WEEKDAYS[d.weekday()]), None)
        first += block["weeks"]
    return None


def _next(blocks: list, start_on, end, after) -> dict | None:
    """The first day from `after` on, before end, with a routine: {date, routine}."""
    d = after
    while d < end:
        routine = _routine_on(blocks, start_on, d)
        if routine is not None:
            return {"date": d.isoformat(), "routine": routine}
        d += timedelta(days=1)
    return None


def program_read(row: Program, today, live_keys: set) -> dict:
    """The program as get_routines gives it. See the module notes."""
    blocks, start = _stored(row), row.start_on
    weeks = total_weeks(blocks)
    end = start + timedelta(weeks=weeks)  # the day after its last
    out = {"id": row.id, "name": row.name, "start_on": start.isoformat(), "weeks": weeks, "week": None,
           "state": "running", "today": None, "next": None, "this_week": None, "blocks": blocks}
    named = []
    if today < start:
        out["state"] = "not_started"
        out["next"] = first = _next(blocks, start, end, start)
        if first is None:  # never: every block holds a day; said plainly all the same
            words = f"{row.name} starts on {formatting.day(start, today)}."
        else:
            on = "" if first["date"] == start.isoformat() else f" on {formatting.day(first['date'], today)}"
            words = f"{row.name} starts on {formatting.day(start, today)}. First: {first['routine']}{on}."
            named.append(first["routine"])
    elif today >= end:
        out["state"] = "ended"
        words = f"{row.name} ended on {formatting.day(end - timedelta(days=1), today)}, after {_weeks_text(weeks)}."
    else:
        week = (today - start).days // 7 + 1
        begins = start + timedelta(weeks=week - 1)
        days = [begins + timedelta(days=i) for i in range(7)]
        out.update(week=week, today=_routine_on(blocks, start, today), next=_next(blocks, start, end,
                                                                                  today + timedelta(days=1)),
                   this_week=[{"date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()],
                               "routine": _routine_on(blocks, start, d)} for d in days])
        words = f"{row.name}, week {week} of {weeks}."
        if out["today"] is not None:
            words += f" Today: {out['today']}."
            named.append(out["today"])
        elif out["next"] is not None:
            words += (f" Today is a rest day. Next: {out['next']['routine']} on "
                      f"{formatting.day(out['next']['date'], today)}.")
            named.append(out["next"]["routine"])
        else:
            words += " Today is a rest day, and no routine day is left."
    words += "".join(f" {name} is not saved now." for name in dict.fromkeys(named) if name_key(name) not in live_keys)
    return {**out, "words": words}
