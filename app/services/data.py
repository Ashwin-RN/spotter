"""Data on request (contract data-on-request.md): two reads over the person's own entries, from the field list.

what_is_logged(user_id)
    Every area of fields.AREAS, in order: what one row is, how many rows the person has (deleted ones left out),
    the first and last dates, the rows in the 28 days ending today and the 28 before, each field as one list in
    FIELD_COLUMNS' order (its name in the person's units, meaning, unit word, stored or derived, its totals, filter,
    group, and the rows that hold a value, all time), and the names logged by field, most used first, at most
    NAMES_MOST a field (a fanned field's keys; for muscle, by weighted sets). Then every open goal (OpenGoal: as
    get_goals works it), the daily and weekly amounts as whoami gives them (targets), how get_data works out each
    total (HOW), and a summary over fields.SUMMARY_AREAS.

get_data(user_id, data)
    One query over one area: data holds area and, each optional, fields, filters, from_date, to_date, group_by,
    totals, order, limit and cursor. A null or a blank text is left out (R2). Every problem of a call comes back at
    once (rules.combine) and nothing is read then. The range is both ends taken, left out the 28 days ending today,
    never before log.oldest_day. Rows are lists under columns, in the person's units, the unit in each column's
    name. With group_by, one row a group: its keys, a week's or month's days and days_logged, then each total.
    Totals are worked from the stored numbers (exact fractions), then converted once to the person's units and
    rounded once at the quantity's shown step (formatting.shown_number). Shares are a group's whole percent of the
    sum over every group, by largest remainder, before order and paging (R8). Under a fanned group (muscle), a
    weighted field's sum and per_day carry the fan's weight (R13); the range totals never do.

    The page: limit rows (100, at most 300) from the cursor's offset, cut further while the answer as compact JSON
    passes CEILING (R7); more is then a cursor: "c1." and base64url of {q: the query's hash, f, t: the range of the
    first page, o: the rows given before}. No state is kept between calls. The open goals the query is about ride
    along (the area's goals hook), with the daily and weekly amounts.

Both are reads: no lock, no write limit, nothing written. Neither names an area or a field: every such word is in
app/services/fields.py, so a later area or field is an entry there, never a branch here (R9).
"""

import base64
import binascii
import dataclasses
import difflib
import hashlib
import json
from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, formatting, metrics, rules, users
from app.mcp_schemas import NEAR_CUTOFF
from app.models import Goal, User
from app.services import goals as goal_reads
from app.services import log
from app.services.common import no_account
from app.services.fields import (
    AREAS, FIELD_COLUMNS, PERIOD_CELLS, ROW_KEYS, SUMMARY_AREAS, TIME_GROUPS, TOTALS, Area, Field, Query, as_sent,
    column, field_named, is_pace, names_of, unit_word,
)

DATE, ID = ROW_KEYS
DAY, WEEK, MONTH = TIME_GROUPS
PERIOD_DAYS, PERIOD_LOGGED = PERIOD_CELLS
SUM, AVG, MAX, MIN, COUNT, PER_DAY, SHARE = TOTALS
LIMIT = (1, 100, 300)       # the least, left out, the most
CEILING = 30_000            # the answer's characters as compact JSON, at most (R7)
WINDOW = 28                 # the range left out, and what_is_logged's windows
NAMES_MOST = 50             # names a field in what_is_logged
CURSOR = "c1."
OPEN_GOAL = ("goal_id", "label", "status", "target_date", "plan_line")  # OpenGoal: GoalNumbers' fields of that name
GOALS_MOST = 10             # the open goals that ride along with get_data, in get_goals' order
CURSOR_MOST = 200           # characters: Spotter's cursors are about 90, so a longer one is never Spotter's
SIZE_NOTE = "The rows stop at the size limit: send more for the rest."
EMPTY_NOTE = "Nothing is logged yet: every area is empty."
HOW = (
    "sum, avg, max and min are over the rows' values, nulls left out, and null when no row holds one. count counts "
    "the rows with a value. per_day is the sum over the days with an entry in the area, in the range or in the "
    "group's week or month: days_logged says how many. For a measurement's value, it is the days with that kind. "
    "share is a group's whole percent of the sum over every group, and the shares sum to 100. Grouped by muscle, a "
    "set counts in each muscle its lift works, 1 for a primary and 0.5 for a secondary, in the sums, per_day and "
    "share, and count counts the sets that work it, so the groups add up to more than the range totals, which count "
    "each set once. A pace's avg is total minutes over total distance. Weeks run Monday to Sunday and months are "
    "calendar months: a week or month group gives its days in range and its days logged. Totals are worked out from "
    "the stored numbers, then shown in the person's units. Deleted entries are never counted."
)


# ---------------------------------------------------------------- small pieces

def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _expand(err: dict) -> list[dict]:
    """A refusal as its parts: a combined one gives back each of its problems."""
    return list(err.get("errors") or [err])


def _count(n: int) -> str:
    return formatting.show(n, "count")


def _plural(n: int, one: str, many: str) -> str:
    return f"{_count(n)} {one if n == 1 else many}"


def _exact(value) -> Fraction:
    """A stored number as an exact fraction of its shortest decimal, so sums and halves are what they read as."""
    return Fraction(value) if isinstance(value, int) else Fraction(repr(float(value)))


def _compact(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _targets(user: User) -> dict:
    """The daily and weekly amounts, as whoami gives them (R5), the daily goals in grams too (macros-and-asking.md)."""
    return {"calorie_target_kcal": user.calorie_target_kcal, "protein_target_g": user.protein_target_g,
            "carbs_target_g": user.carbs_target_g, "fat_target_g": user.fat_target_g,
            "target_sessions_per_week": user.target_sessions_per_week,
            "target_cardio_minutes_per_week": user.target_cardio_minutes_per_week}


def _open_goals(s, user: User, today: date, units, kinds=None) -> list[dict]:
    """The open goals' numbers, as get_goals reads them (goal_numbers, then rounded_numbers), of these kinds."""
    query = (select(Goal).where(Goal.user_id == user.id, Goal.status == "open").order_by(Goal.id)
             .options(selectinload(Goal.exercise)))
    rows = list(s.scalars(query if kinds is None else query.where(Goal.kind.in_(kinds))))
    loaded = goal_reads.load_numbers(s, user.id, today, rows)
    return goal_reads.rounded_numbers(goal_reads.goal_numbers(rows, loaded, today, units=units))


def _open_goal(numbers: dict) -> dict:
    return {key: numbers[key] for key in OPEN_GOAL}


def _span(start: date, end: date, today: date) -> str:
    return ("on " if start == end else "from ") + formatting.days(start, end, today)


# ---------------------------------------------------------------- what_is_logged

def what_is_logged(user_id: int) -> dict:
    """See the module notes."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        units = users.units_for(user)
        areas = [_logged(s, user, area, today, units) for area in AREAS.values()]
        goals = [_open_goal(n) for n in _open_goals(s, user, today, units)]
        out = {"today": today.isoformat(), "field_columns": list(FIELD_COLUMNS), "areas": areas, "goals": goals,
               "targets": _targets(user), "how": HOW, "summary": _logged_summary(areas, len(goals), today)}
        if all(a["entries"] == 0 for a in areas):
            out["note"] = EMPTY_NOTE
        return out


def _logged(s, user: User, area: Area, today: date, units) -> dict:
    """One area as what_is_logged gives it."""
    rows = area.load(s, user, None, None)
    dates = sorted(area.logged_days(s, user, None, None)) if area.entries_are_days else [r[DATE] for r in rows]
    ages = [(today - metrics.on_or_before(d, today)).days for d in dates]
    out = {"area": area.name, "meaning": area.meaning, "entries": len(dates),
           "first": min(dates).isoformat() if dates else None, "last": max(dates).isoformat() if dates else None,
           "last_28d": sum(1 for a in ages if a < WINDOW),
           "prior_28d": sum(1 for a in ages if WINDOW <= a < 2 * WINDOW),
           "fields": [[column(f, units), f.meaning, unit_word(f, units), "stored" if f.stored else "derived",
                       ", ".join((COUNT, *f.totals)), f.filter, f.group,
                       sum(1 for r in rows if f.value(r) is not None)] for f in area.fields]}
    names = {f.name: _names(f, rows) for f in area.fields if f.filter or f.group}
    names = {name: held for name, held in names.items() if held}
    if names:
        out["names"] = names
    return out


def _names(f: Field, rows: list) -> list[str]:
    """A field's names logged, all time, most rows first (weighted for a fan), ties by name; text only."""
    tally: dict = {}
    by_key: dict = {}
    for r in rows:
        if f.fan is not None:
            for key, weight in f.fan(r):
                tally[key] = tally.get(key, 0) + _exact(weight)
            continue
        key = f.key(r) if f.key else f.value(r)
        if key is None or isinstance(key, bool):
            continue
        tally[key] = tally.get(key, 0) + 1
        by_key.setdefault(key, []).append(r)
    named = [(f.shown(by_key[k]) if f.shown and k in by_key else k, n) for k, n in tally.items()]
    named = [(name, n) for name, n in named if isinstance(name, str)]
    return [name for name, _ in sorted(named, key=lambda p: (-p[1], p[0]))[:NAMES_MOST]]


def _logged_summary(areas: list[dict], goals: int, today: date) -> str:
    """'{entries} entries in {areas} areas, from {first day} to {last day}. {n} open goals.'"""
    counted = [a for a in areas if a["area"] in SUMMARY_AREAS and a["entries"]]
    entries = sum(a["entries"] for a in counted)
    goal_text = _plural(goals, "open goal", "open goals") + "." if goals else "No open goals."
    if not entries:
        return "No entries yet. " + goal_text
    first = min(a["first"] for a in counted)
    last = max(a["last"] for a in counted)
    return (f"{_plural(entries, 'entry', 'entries')} in {_plural(len(counted), 'area', 'areas')}, from "
            f"{formatting.day(first, today)} to {formatting.day(last, today)}. {goal_text}")


# ---------------------------------------------------------------- get_data: checking the call

class Plan:
    """A checked call: the area, the query a hook sees, and how the answer is laid out."""

    def __init__(self, **values):
        self.__dict__.update(values)


def _no_area(raw) -> dict:
    names = list(AREAS)
    near = difflib.get_close_matches(raw.strip(), names, n=1, cutoff=0.6) if isinstance(raw, str) else []
    return rules.error("invalid_field", f"area {as_sent(raw)} is not an area Spotter reads.",
                       f"Use one of: {', '.join(names)}." + (f" The nearest is {near[0]}." if near else ""))


def _no_field(area: Area, name, units) -> dict:
    taken = [column(f, units) for f in area.fields]
    near = (difflib.get_close_matches(name, taken, n=1, cutoff=NEAR_CUTOFF)
            if isinstance(name, str) and len(name) <= rules.NAME_MAX else [])
    nearest = f" The nearest field is {near[0]}." if near else ""
    return rules.error("invalid_field", f"{as_sent(name)} is not a field of {area.name}.",
                       f"{area.name} takes: {', '.join(taken)}.{nearest}")


def _sent_day(name: str, raw, errors: list) -> date | None:
    if _blank(raw):
        return None
    day = rules.to_date(raw) if isinstance(raw, str) else None
    if day is None:
        errors.append(rules.error("invalid_date", f"{name} {as_sent(raw)} is not a date.",
                                  f"Give {name} as YYYY-MM-DD, or leave it out."))
    return day


def _dates(data: dict, today: date, floor: date, errors: list) -> tuple[date | None, date | None]:
    """from_date and to_date as sent, each checked: a real day, not after today, not before the account's floor."""
    start = _sent_day("from_date", data.get("from_date"), errors)
    end = _sent_day("to_date", data.get("to_date"), errors)
    for name, day in (("from_date", start), ("to_date", end)):
        if day is not None and day < floor:
            errors.append(rules.error("date_too_old", f"{name} {day.isoformat()} is before {floor.isoformat()}, the "
                                                      "oldest day this account can hold.",
                                      f"Give a date from {floor.isoformat()} to today, or leave {name} out."))
    if end is not None and end > today:
        errors.append(rules.error("date_in_future", f"to_date {end.isoformat()} is after today ({today.isoformat()}).",
                                  "Give to_date as today or earlier, or leave it out for today."))
    elif start is not None and end is not None and start > end:
        errors.append(rules.error("invalid_field", f"from_date {start.isoformat()} is after to_date {end.isoformat()}.",
                                  "Give from_date on or before to_date."))
    elif start is not None and end is None and start > today:
        errors.append(rules.error("date_in_future",
                                  f"from_date {start.isoformat()} is after today ({today.isoformat()}).",
                                  "Give from_date as today or earlier, or leave it out."))
    return start, end


def _cursor_of(raw) -> dict | None:
    """A cursor Spotter gave, decoded: {q, f, t, o}; else None."""
    if not isinstance(raw, str) or len(raw) > CURSOR_MOST or not raw.startswith(CURSOR):
        return None
    body = raw[len(CURSOR):]
    try:
        held = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
        first, last = date.fromisoformat(held["f"]), date.fromisoformat(held["t"])
        offset, digest = held["o"], held["q"]
    except (binascii.Error, ValueError, TypeError, KeyError, UnicodeDecodeError, AttributeError, RecursionError):
        return None
    if not isinstance(digest, str) or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 \
            or first > last:
        return None
    return {"q": digest, "f": first, "t": last, "o": offset}


def _cursor(digest: str, first: date, last: date, offset: int) -> str:
    text = _compact({"q": digest, "f": first.isoformat(), "t": last.isoformat(), "o": offset})
    return CURSOR + base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _limit(raw, errors: list) -> int:
    lo, default, hi = LIMIT
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
        errors.append(rules.error("invalid_field", f"limit {as_sent(raw)} is outside {lo} to {hi}.",
                                  f"Give limit from {lo} to {hi}."))
        return default
    return raw


def _fields(area: Area, data: dict, grouped: bool, units, errors: list) -> list[Field] | None:
    sent = data.get("fields")
    if sent is None:
        return None
    if not isinstance(sent, list):
        errors.append(rules.error("invalid_field", f"fields must be a list, not {as_sent(sent)}.",
                                  "Send fields as a list."))
        return None
    if not sent:
        errors.append(rules.error("invalid_field", "fields holds no fields.",
                                  "Name at least one field, or leave fields out for every field."))
    asked = []
    for name in sent:
        f = field_named(area, name)
        if f is None:
            errors.append(_no_field(area, name, units))
        else:
            asked.append(f)
    if grouped:
        errors.append(rules.error("invalid_field", "fields and group_by do not go together: with group_by each row is "
                                                   "a group.",
                                  "Leave fields out with group_by, and name in totals what to work out for each "
                                  "group."))
    return asked


def _filters(area: Area, data: dict, errors: list) -> dict:
    sent = data.get("filters")
    if not sent:
        return {}
    if not isinstance(sent, dict):
        errors.append(rules.error("invalid_field", f"filters must be an object of named fields, not {as_sent(sent)}.",
                                  "Send filters as an object, such as {\"exercise\": \"Back Squat\"}."))
        return {}
    taken = [f.name for f in area.fields if f.filter]
    if not taken:
        errors.append(rules.error("invalid_field", f"{area.name} takes no filters.",
                                  f"Leave filters out for {area.name}."))
        return {}
    matched = {}
    for key, value in sent.items():
        f = field_named(area, key)
        if f is None or not f.filter:
            errors.append(rules.error("invalid_field", f"{as_sent(key)} is not a filter of {area.name}.",
                                      f"{area.name} filters on: {', '.join(taken)}."))
        elif not _blank(value):
            held, err = f.match(value)
            if err:
                errors.extend(_expand(err))
            else:
                matched[f.name] = held
    return matched


def _groups(area: Area, data: dict, errors: list) -> list[str]:
    sent = data.get("group_by")
    if not sent:
        return []
    if not isinstance(sent, list):
        errors.append(rules.error("invalid_field", f"group_by must be a list, not {as_sent(sent)}.",
                                  "Send group_by as a list."))
        return []
    taken = [*area.groups, *(f.name for f in area.fields if f.group)]
    if not taken:
        errors.append(rules.error("invalid_field", f"{area.name} takes no group_by.",
                                  f"Leave group_by out for {area.name}."))
        return []
    lo, hi = 1, 2
    if len(sent) > hi:
        errors.append(rules.error("invalid_field", f"group_by holds {len(sent)} items: it takes {lo} to {hi}.",
                                  f"Send {lo} to {hi} items in group_by."))
    kept = []
    for name in sent:
        if name not in taken:
            errors.append(rules.error("invalid_field", f"{as_sent(name)} is not a group of {area.name}.",
                                      f"{area.name} groups by: {', '.join(taken)}."))
        elif name in kept:
            errors.append(rules.error("invalid_field", f"group_by names {name} twice.", "Name each group once."))
        else:
            kept.append(name)
    times = [name for name in kept if name in TIME_GROUPS]
    if len(times) > 1:
        errors.append(rules.error("invalid_field", f"group_by holds {times[0]} and {times[1]}: one of day, week and "
                                                   "month at most.", "Keep one of day, week and month in group_by."))
    return kept


def _totals(area: Area, data: dict, query: Query, grouped: bool, units, errors: list) -> list[tuple[Field, str]]:
    sent = data.get("totals")
    if not sent:
        return []
    if not isinstance(sent, dict):
        errors.append(rules.error("invalid_field", f"totals must be an object of named fields, not {as_sent(sent)}.",
                                  "Send totals as an object, such as {\"kcal\": [\"sum\"]}."))
        return []
    asked, shared = [], False
    for key, value in sent.items():
        f = field_named(area, key)
        if f is None:
            errors.append(_no_field(area, key, units))
            continue
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(rules.error("invalid_field", f"totals.{key} takes a list of totals, not {as_sent(value)}.",
                                      f"Send totals.{key} as a list, such as [\"sum\"]."))
            continue
        if not value:
            errors.append(rules.error("invalid_field", f"totals.{key} holds no totals.",
                                      f"Name at least one total for {key}, or leave it out."))
        taken = (COUNT, *f.totals)
        for total in value:
            if not isinstance(total, str) or total not in TOTALS:
                errors.append(rules.error("invalid_field", f"{as_sent(total)} is not a total.",
                                          f"Use: {', '.join(TOTALS)}."))
            elif total not in taken:
                errors.append(rules.error("invalid_field", f"{key} takes no {total}.", f"{key} takes: "
                                                                                      f"{', '.join(taken)}."))
            elif total == SHARE and not grouped:
                shared = True
            elif (refused := f.guard(total, query) if f.guard else None) is not None:
                if refused not in errors:
                    errors.append(refused)
            else:
                asked.append((f, total))
    if shared:
        errors.append(rules.error("invalid_field", "share needs group_by: it is a group's part of the range.",
                                  "Send group_by with share, or leave share out."))
    return asked


def _layout(area: Area, shown: list[Field], groups: list[str], totals: list, units) -> tuple[list[str], dict]:
    """The answer's columns in these units, and every other spelling of each (the other unit's), to its column."""
    columns, other = [], {}
    if not groups:
        for f in shown:
            columns.append(column(f, units))
            other.update(dict.fromkeys(names_of(f), column(f, units)))
        return columns, other
    for name in groups:
        f = field_named(area, name)
        columns.append(name if f is None else column(f, units))
    if any(name in (WEEK, MONTH) for name in groups):
        columns += list(PERIOD_CELLS)
    for f, total in totals:
        columns.append(f"{column(f, units)}_{total}")
        other.update({f"{name}_{total}": f"{column(f, units)}_{total}" for name in names_of(f)})
    return columns, other


def _plan(data: dict, today: date, floor: date, units) -> "Plan | dict":
    """The call checked: a Plan, or every problem at once."""
    errors: list[dict] = []
    raw_area = data.get("area")
    area = None
    if _blank(raw_area):
        errors.append(rules.error("missing_field", "area is missing.", "Give area, as what_is_logged names it."))
    else:
        area = AREAS.get(raw_area.strip()) if isinstance(raw_area, str) else None
        if area is None:
            errors.append(_no_area(raw_area))
    start_sent, end_sent = _dates(data, today, floor, errors)
    raw_cursor = data.get("cursor")
    held = None if _blank(raw_cursor) else _cursor_of(raw_cursor)
    if held is not None and (held["f"] < floor or held["t"] > today):  # never a range Spotter gave
        held = None
    if not _blank(raw_cursor) and held is None:
        errors.append(rules.error("invalid_field", "cursor is not one Spotter gave.",
                                  "Send more from the last get_data answer as it came, or leave cursor out to start "
                                  "from the first rows."))
    limit = _limit(data.get("limit"), errors)
    if area is None:
        return rules.combine(errors)

    grouped = bool(data.get("group_by"))
    asked = _fields(area, data, grouped, units, errors)
    filters = _filters(area, data, errors)
    groups = _groups(area, data, errors)
    if held is not None:
        start, end = held["f"], held["t"]
    else:
        end = end_sent or today
        start = start_sent or max(end - timedelta(days=WINDOW - 1), floor)
    query = Query(area.name, (), filters, tuple(groups), (), start, end, today, units)
    totals = _totals(area, data, query, grouped, units, errors)
    sent_totals = list(totals)
    if groups and not totals:
        totals = [(field_named(area, area.count_field), COUNT)]
    shown = asked if asked is not None else [f for f in area.fields if f.default and f.name != area.count_field]
    columns, other = _layout(area, shown, groups, totals, units)

    order = None
    raw_order = data.get("order")
    if not _blank(raw_order) and columns:  # no columns: the fields' own refusal says why
        text = raw_order.strip() if isinstance(raw_order, str) else ""
        desc = text.startswith("-")
        name = text[1:] if desc else text
        name = name if name in columns else other.get(name)
        if name is None:
            errors.append(rules.error("invalid_field", f"order {as_sent(raw_order)} is not a column of this answer.",
                                      f"Order by one of: {', '.join(columns)}, or with a minus sign first, such as "
                                      f"-{columns[-1]}."))
        else:
            order = (columns.index(name), desc)
    if errors:
        return rules.combine(errors)

    base, _ = _layout(area, shown, groups, totals, formatting.KG_KM)
    canon = {"area": area.name, "fields": None if asked is None else [f.name for f in asked],
             "filters": {k: sorted(v) if isinstance(v, frozenset) else v for k, v in filters.items()},
             "group_by": groups, "totals": [[f.name, total] for f, total in sent_totals],
             "order": None if order is None else ("-" if order[1] else "") + base[order[0]]}
    digest = hashlib.sha256(json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                            .encode("utf-8")).hexdigest()[:16]
    if held is not None and (held["q"] != digest or start_sent not in (None, held["f"])
                             or end_sent not in (None, held["t"])):
        return rules.combine([rules.error(
            "cursor_changed", "This cursor is for another query.",
            "Send the cursor with the same area, fields, filters, from_date, to_date, group_by, totals and order as "
            "the call that gave it, or leave cursor out to start again.")])
    query = Query(area.name, tuple(f.name for f in shown), filters, tuple(groups),
                  tuple((f.name, total) for f, total in totals), start, end, today, units)
    return Plan(area=area, query=query, shown=shown, groups=groups, totals=totals, columns=columns, order=order,
                limit=limit, offset=0 if held is None else held["o"], digest=digest,
                from_left_out=start_sent is None, to_left_out=end_sent is None)


# ---------------------------------------------------------------- get_data: working it out

def _effective(row: dict, today: date) -> date:
    return metrics.on_or_before(row[DATE], today)


def _passes(area: Area, query: Query, row: dict) -> bool:
    for name, held in query.filters.items():
        f = field_named(area, name)
        if f.fan is not None:
            if not {key for key, _ in f.fan(row)} & held:
                return False
        elif isinstance(held, bool):
            if f.value(row) is not held:
                return False
        elif f.value(row) not in held:
            return False
    return True


def _sorted(items: list, keys: list) -> list:
    """Stable sort by each (value of, largest first) in turn, the first deciding most; nulls last either way."""
    for value_of, desc in reversed(keys):
        held = [i for i in items if value_of(i) is not None]
        empty = [i for i in items if value_of(i) is None]
        items = sorted(held, key=value_of, reverse=desc) + empty
    return items


def _quantity(f: Field, total: str | None, rows: list) -> str | None:
    if total is not None and total in f.total_quantity:
        return f.total_quantity[total]
    if f.row_quantity is not None and rows:
        return f.row_quantity(rows[0])
    return f.quantity


def _cell(f: Field, value, row: dict, units):
    """A row's value as its cell: a number at its step in the person's units, a pace as m:ss, a date as text."""
    if value is None:
        return None
    if is_pace(f):
        return formatting.show(value, "pace", unit=False, units=units)
    quantity = _quantity(f, None, [row])
    if quantity is None:
        return value.isoformat() if isinstance(value, date) else value
    return formatting.shown_number(value, quantity, units=units)


def _raw(area: Area, f: Field, total: str, members: list, days: int, weighted: bool, today: date):
    """A total of these (row, weight) members, exact, before units and rounding. days: per_day's divisor."""
    held = [(f.value(r), w, r) for r, w in members]
    held = [(v, w, r) for v, w, r in held if v is not None]
    if total == COUNT:
        return len(held)
    if total == AVG and f.ratio is not None:
        top, bottom = (field_named(area, name) for name in f.ratio)
        pairs = [(top.value(r), bottom.value(r)) for r, _ in members]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        under = sum((_exact(b) for _, b in pairs), Fraction(0))
        return sum((_exact(a) for a, _ in pairs), Fraction(0)) / under if pairs and under else None
    if not held:
        return None
    numbers = [_exact(v) for v, _, _ in held]
    if total in (SUM, PER_DAY, SHARE):
        whole = sum((n * (w if weighted and f.weighted else 1) for n, (_, w, _) in zip(numbers, held)), Fraction(0))
        if total != PER_DAY:
            return whole
        if f.per_day_by != "area":
            days = len({_effective(r, today) for _, _, r in held})
        return whole / days if days else None
    if total == AVG:
        return sum(numbers, Fraction(0)) / len(numbers)
    return max(numbers) if total == MAX else min(numbers)


def _total_cell(f: Field, total: str, value, rows: list, units):
    if value is None or total in (COUNT, SHARE):
        return value
    if is_pace(f):
        return formatting.show(float(value), "pace", unit=False, units=units)
    return formatting.shown_number(float(value), _quantity(f, total, rows), units=units)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _periods(level: str, start: date, end: date) -> list:
    """Every period of the range, newest first: a day, a week's Monday, a month's first day."""
    if level == DAY:
        return [end - timedelta(days=n) for n in range((end - start).days + 1)]
    if level == WEEK:
        weeks = (_monday(end) - _monday(start)).days // 7
        return [_monday(end) - timedelta(days=7 * n) for n in range(weeks + 1)]
    out, month = [], end.replace(day=1)
    while month >= start.replace(day=1):
        out.append(month)
        month = (month - timedelta(days=1)).replace(day=1)
    return out


def _period_of(level: str, d: date) -> date:
    return d if level == DAY else _monday(d) if level == WEEK else d.replace(day=1)


def _period_days(level: str, key: date, start: date, end: date) -> set[date]:
    if level == DAY:
        last = key
    elif level == WEEK:
        last = key + timedelta(days=6)
    else:
        last = (key + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    first, last = max(key, start), min(last, end)
    return {first + timedelta(days=n) for n in range((last - first).days + 1)} if first <= last else set()


def _period_cell(level: str, key: date) -> str:
    return key.isoformat()[:7] if level == MONTH else key.isoformat()


class _Level:
    """One group of group_by: a time group, or a field (its key, its fan, its shown cell)."""

    def __init__(self, area: Area, name: str, query: Query, kept: list):
        self.name = name
        self.time = name in TIME_GROUPS
        self.field = None if self.time else field_named(area, name)
        self.periods = _periods(name, query.start, query.end) if self.time else []
        self.only = query.filters.get(name) if self.field is not None and self.field.fan else None
        self.names: dict = {}
        if self.field is not None and self.field.shown is not None:
            rows_of: dict = {}
            for r in kept:
                rows_of.setdefault(self.field.key(r), []).append(r)
            self.names = {key: self.field.shown(rows) for key, rows in rows_of.items()}

    def keys(self, row: dict, today: date) -> list[tuple[object, Fraction]]:
        if self.time:
            return [(_period_of(self.name, _effective(row, today)), Fraction(1))]
        f = self.field
        if f.fan is not None:
            keys = [(k, _exact(w)) for k, w in f.fan(row) if self.only is None or k in self.only]
            return keys or [(None, Fraction(1))]
        return [(f.key(row) if f.key else f.value(row), Fraction(1))]

    def cell(self, key):
        if key is None:
            return None
        if self.time:
            return _period_cell(self.name, key)
        return self.names.get(key, key)


def _grouped(area: Area, plan: Plan, kept: list, logged: set[date], units) -> list[dict]:
    """One row a group, in the default order (R8's shares worked over them all): {cells, sorts, rows}."""
    query = plan.query
    levels = [_Level(area, name, query, kept) for name in plan.groups]
    fanned = any(level.field is not None and level.field.fan is not None for level in levels)
    timed = next((level for level in levels if level.time), None)
    first = plan.totals[0]

    def divisor(prefix: tuple) -> int:
        if timed is None:
            return len(logged)
        key = prefix[levels.index(timed)] if len(prefix) > levels.index(timed) else None
        return len(logged) if key is None else len(logged & _period_days(timed.name, key, query.start, query.end))

    def build(members: list, depth: int, prefix: tuple) -> list:
        if depth == len(levels):
            return [(prefix, members)]
        level = levels[depth]
        if level.time:
            buckets = {p: [] for p in level.periods}
            for r, w in members:
                buckets[level.keys(r, query.today)[0][0]].append((r, w))
            return [item for p in level.periods for item in build(buckets[p], depth + 1, prefix + (p,))]
        buckets: dict = {}
        for r, w in members:
            for key, weight in level.keys(r, query.today):
                buckets.setdefault(key, []).append((r, w * weight))
        if not buckets:
            return [(prefix + (None,) * (len(levels) - depth), [])] if depth else []
        f, total = first
        lead = SUM if total == SHARE else total

        def rank(key):
            value = _raw(area, f, lead, buckets[key], divisor(prefix), fanned, query.today)
            text = level.cell(key)
            return (value is None, -(value or 0), text is None, str(text or ""))

        return [item for key in sorted(buckets, key=rank) for item in build(buckets[key], depth + 1, prefix + (key,))]

    out = []
    for keys, members in build([(r, Fraction(1)) for r in kept], 0, ()):
        rows = [r for r, _ in members]
        days = divisor(keys)
        cells = [level.cell(key) for level, key in zip(levels, keys)]
        if timed is not None and timed.name != DAY:
            span = _period_days(timed.name, keys[levels.index(timed)], query.start, query.end)
            cells += [len(span), len(span & logged)]
        raws = [_raw(area, f, total, members, days, fanned, query.today) for f, total in plan.totals]
        weights = [_raw(area, f, SUM, members, days, fanned, query.today) if total == SHARE else None
                   for f, total in plan.totals]
        out.append({"cells": cells, "raws": raws, "weights": weights, "rows": rows})
    for i, (f, total) in enumerate(plan.totals):
        if total == SHARE:
            for row, share in zip(out, _shares([row["weights"][i] for row in out])):
                row["raws"][i] = share
    for row in out:
        totals = [_total_cell(f, total, raw, row["rows"], units) for (f, total), raw in zip(plan.totals, row["raws"])]
        row["sorts"] = row["cells"] + [None if raw is None else float(raw) for raw in row["raws"]]
        row["cells"] = row["cells"] + totals
    return out


def _shares(sums: list) -> list:
    """Whole percents of the sum over every group, summing to 100 by largest remainder: a tie goes to the larger
    value, then the earlier group. A null sum has a null share; nothing to share gives every share null."""
    whole = sum((s for s in sums if s is not None), Fraction(0))
    if not whole:
        return [None] * len(sums)
    exact = [None if s is None else s * 100 / whole for s in sums]
    shares = [None if e is None else int(e) for e in exact]
    left = 100 - sum(s for s in shares if s is not None)
    ranked = sorted((i for i, e in enumerate(exact) if e is not None),
                    key=lambda i: (-(exact[i] - shares[i]), -sums[i], i))
    for i in ranked[:left]:
        shares[i] += 1
    return shares


def _plain(area: Area, plan: Plan, kept: list, units) -> list[dict]:
    """One row a row of the area, in the area's default order."""
    keys = []
    for key in area.order:
        name = key.lstrip("-")
        f = field_named(area, name)
        keys.append(((f.value if f else (lambda r, name=name: r.get(name))), key.startswith("-")))
    out = []
    for r in _sorted(kept, keys):
        values = [f.value(r) for f in plan.shown]
        out.append({"cells": [_cell(f, v, r, units) for f, v in zip(plan.shown, values)], "sorts": values})
    return out


def _range_totals(area: Area, plan: Plan, kept: list, logged: set[date], units) -> dict | None:
    """The totals over the whole range, each row once, every total but share (R12: none asked, none given)."""
    asked = [(f, total) for f, total in plan.totals if total != SHARE]
    if not asked:
        return None
    members = [(r, Fraction(1)) for r in kept]
    # A range total is one figure over every row: a field's guard is asked of the query with no groups, and where it
    # refuses (value over several kinds, say), the cell is null rather than a figure that mixes units.
    whole = dataclasses.replace(plan.query, groups=())
    raws = [None if f.guard is not None and f.guard(total, whole) is not None
            else _raw(area, f, total, members, len(logged), False, plan.query.today) for f, total in asked]
    return {"columns": [f"{column(f, plan.query.units)}_{total}" for f, total in asked],
            "values": [_total_cell(f, total, raw, kept, units) for (f, total), raw in zip(asked, raws)]}


def _nothing(s, user: User, area: Area, query: Query) -> str:
    """The note when nothing in range matches: the last one's date, or that nothing is logged."""
    every = area.load(s, user, None, None)
    matching = [r for r in every if _passes(area, query, r)]
    span = _span(query.start, query.end, query.today)
    if matching and area.ranged:
        last = max(r[DATE] for r in matching)
        return f"Nothing in {area.name} {span}. The last one is on {formatting.day(last, query.today)}."
    if every:
        return f"Nothing in {area.name} matches these filters yet."
    return f"Nothing is logged in {area.name} yet."


def _summary(plan: Plan, total: int, offset: int, n: int, more: bool) -> str:
    query = plan.query
    one, many = ("group", "groups") if plan.groups else plan.area.noun
    span = _span(query.start, query.end, query.today)
    if not total:
        return f"No {many} {span}."
    head = f"{_plural(total, one, many)} {span}"
    if not n:
        return f"{head}, none here: the cursor is past the last row."
    if not offset and n == total:
        return f"{head}, all here."
    return f"{head}, rows {_count(offset + 1)} to {_count(offset + n)} here." + (" Send more for the rest." if more
                                                                               else "")


def _range_note(plan: Plan) -> str | None:
    if not plan.from_left_out:
        return None
    q = plan.query
    days = _plural((q.end - q.start).days + 1, DAY, PERIOD_DAYS)
    if plan.to_left_out and q.end == q.today:
        return f"No range was given: this is the last {days}, {formatting.days(q.start, q.end, q.today)}."
    return f"No from_date was given: this is the {days} ending {formatting.days(q.end, q.end, q.today)}."


def get_data(user_id: int, data: dict) -> dict:
    """See the module notes."""
    data = data if isinstance(data, dict) else {}
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        units = users.units_for(user)
        plan = _plan(data, today, log.oldest_day(user), units)
        if isinstance(plan, dict):
            return plan
        return _answer(s, user, plan, units)


def _narrowed(s, user: User, area: Area, query: Query) -> Query:
    """The query with each filter whose field narrows (Field.narrow) narrowed by the person's own rows, all time: a
    plain word on family takes the families of the candidates this person logged (exercise-list.md R18). The rows
    are the person's alone, so another account's lifts never widen it."""
    hooks = {name: f.narrow for name in query.filters if (f := field_named(area, name)) is not None and f.narrow}
    if not hooks:
        return query
    every = area.load(s, user, None, None)
    return dataclasses.replace(query, filters={**query.filters,
                                               **{name: narrow(query.filters[name], every)
                                                  for name, narrow in hooks.items()}})


def _answer(s, user: User, plan: Plan, units) -> dict:
    plan.query = _narrowed(s, user, plan.area, plan.query)
    area, query = plan.area, plan.query
    rows = area.load(s, user, query.start, query.end)
    logged = (area.logged_days(s, user, query.start, query.end) if area.logged_days
              else {_effective(r, query.today) for r in rows})
    logged = {d for d in logged if query.start <= d <= query.end}
    kept = [r for r in rows if _passes(area, query, r)]
    table = [] if not kept else (_grouped(area, plan, kept, logged, units) if plan.groups
                                 else _plain(area, plan, kept, units))
    if plan.order is not None:
        at, desc = plan.order
        table = _sorted(table, [(lambda row: row["sorts"][at], desc)])
    goals, more_goals = [], None
    if area.goal_kinds:
        riding = area.goals(_open_goals(s, user, query.today, units, area.goal_kinds), query)
        goals = [_open_goal(n) for n in riding[:GOALS_MOST]]
        if len(riding) > GOALS_MOST:
            past = _plural(len(riding) - GOALS_MOST, "more open goal", "more open goals")
            more_goals = f"{past}: get_goals has them all."
    head = {"area": area.name, "from": query.start.isoformat(), "to": query.end.isoformat()}
    note = _range_note(plan)
    if note:
        head["range_note"] = note
    head.update({"days_in_range": (query.end - query.start).days + 1, "days_logged": len(logged),
                 "columns": plan.columns})
    tail = {}
    totals = _range_totals(area, plan, kept, logged, units)
    if totals is not None:
        tail["totals"] = totals
    empty = _nothing(s, user, area, query) if not kept else None
    return _paged(plan, head, tail, goals, _targets(user), [row["cells"] for row in table], empty, more_goals)


def _paged(plan: Plan, head: dict, tail: dict, goals: list, targets: dict, cells: list, empty: str | None,
           more_goals: str | None = None) -> dict:
    """The answer at the page the cursor and limit give, cut while it passes CEILING (R7), never below one row, so
    more always moves on. more_goals: the note on the open goals past GOALS_MOST, after any other note."""
    total, offset = len(cells), plan.offset
    page = cells[offset:offset + plan.limit]
    sizes = [len(_compact(row)) + 1 for row in page]

    def answer(n: int) -> dict:
        more = offset + n < total
        out = {**head, "rows": page[:n], **tail}
        if more:
            out["more"] = _cursor(plan.digest, plan.query.start, plan.query.end, offset + n)
        out.update({"goals": goals, "targets": targets, "summary": _summary(plan, total, offset, n, more)})
        note = " ".join(text for text in (SIZE_NOTE if n < len(page) else empty, more_goals) if text)
        if note:
            out["note"] = note
        return out

    n = len(page)
    out = answer(n)
    while n > 1 and (over := len(_compact(out)) - CEILING) > 0:
        cut = 0
        while n > 1 and cut < over:
            n -= 1
            cut += sizes[n]
        out = answer(n)
    return out
