"""Food: what a person ate, the foods they keep, and finding a food to log.

log_food(user_id, data, by)
    data: entries [{description, meal?, kcal?, estimated?, portion?, protein_g?, carbs_g?, fat_g?, macros_estimated?,
    saved_food_id?, servings?, usda_fdc_id?, grams?}] (1 to 30), date? (one date for the call: blank = the person's
    today; never after today; at most two years back), allow_repeat?
    - The tag is the server's (rule 13): saved_food when a saved_food_id is given, usda when a
      usda_fdc_id is given, claude_estimate when estimated is true, else you_said. source is never
      an input.
    - A number and an id never travel together (rule 14): kcal with either id is invalid_field. A
      saved food is logged by id and servings (default 1); a USDA food by id and grams. The app works
      out kcal, stored as a whole number and held to the same 0 to 5,000 a stated number is.
    - description is optional with saved_food_id or usda_fdc_id: it defaults to the saved food's
      name, or the USDA food's description (decision 14). The count goes in servings or grams.
    - USDA ids are looked up before the user row is locked: no network call under the lock.
    - Macros (contract macros-and-asking.md): protein_g, carbs_g and fat_g in grams, each null when not logged, never
      0 g. With a number they go as sent, kept at 1 dp, marked macros_estimated: false only when sent false, left out
      true (an estimate the person can confirm), and null when the entry holds no macro, whatever was sent. Never
      with an id: a saved food's are its grams a serving times the servings, a USDA food's its grams a 100 g times
      the grams, marked false, and a macro the food has no figure for is null. A worked-out macro is held to the
      range a sent one is, as a worked-out kcal is.
    - portion: how much was eaten, in the person's words, 1 to 64 characters, kept on any entry. An estimate needs
      it: one with none is refused with missing_field (rules.portion_missing), its fix asking the person once, and
      nothing is written. A blank portion on any other entry is left out.
    - The duplicate guard's entry_tuple is as before: macros and portion are not in it, so the import's keys, built
      from it, still match the rows they wrote.
    - The call log: a call whose checks pass notes atwater= with each entry that sent kcal above 0 and all three
      macros: its ratio of 4 x protein + 4 x carbs + 9 x fat to its kcal, at 2 dp, joined by commas and cut at a
      whole item to the room the notes column has left (calllog.room, after log's kind= and word=). No value, no
      food. Noted here, so a log call of kind food notes it too.
    - A meal slot not given comes from the person's clock (users.now_for): 04:00 to 11:00 breakfast,
      11:00 to 16:00 lunch, 16:00 to 22:00 dinner, 22:00 to 04:00 snack.
    - All or nothing: every entry is checked first, every problem comes back at once.
    - The duplicate guard compares the whole set: the same date and the same
      entries within 10 minutes return the first set with duplicate true.

save_food(user_id, data, by[, drawn_at])
    data: name, serving_label, kcal_per_serving, grams_per_serving?. The same name (casefolded)
    updates the saved food; a deleted one comes back live.
    - A save that changes a food that exists keeps the food as it stood (common.keep_version, contract
      edit-history.md): restore_entry with undo_edit puts it back. A save that changes nothing writes nothing.
    - drawn_at, from the web's edit form only: the food's updated_at as its page drew it. A food that changed
      since is refused with entry_changed, and nothing is saved.

find_food(user_id, query, limit=8)
    A read. The person's saved foods whose name holds the query, most logged first, then by name;
    their own you_said and claude_estimate entries whose description holds it, grouped by the
    casefolded description, most frequent first (an imported entry never: logged_by "import");
    then USDA when the key is set.
    Throttled (decision 13): more than 120 find_food calls in 10 minutes, counted on the person's own
    call-log rows as the write limit counts, is too_many_reads, before any other work.

list_saved_foods(user_id)
    A read for the Foods page: every saved food, live first by name, then the deleted ones, each with
    times_logged and last_logged worked out as find_food does (_usage), so the two agree.

save_food with items (a saved meal, contract macros-and-asking.md): 2 to 30 items, each a saved food (saved_food_id,
    servings), a USDA food (usda_fdc_id, grams) or the person's own (description, kcal, the macros), checked as a food
    entry is. A meal inside a meal, or an item sent with estimated true, is refused. Stored as JSON, each item as
    resolved: description, kcal, the three macros, source and its ids and amounts. kcal_per_serving and the macros a
    serving are the sums, sent by no one: a macro is null when any item lacks it. serving_label defaults to "1 meal".
    A meal is logged by its id as one saved food. A plain save over a saved food keeps a macro a serving left out,
    and one sent as null clears it. grams_per_serving goes the same way: left out of a plain save, a saved food keeps
    its grams and a new one has none; sent as null or "", they are cleared. A save with items clears grams left out:
    a meal's numbers come from its items. A save of a live meal's name with no items is refused with nothing
    written: the meal is changed by sending its items. A deleted food brought back by a plain save takes only the
    macros and grams sent.

day_numbers(user, food_rows, cardio_rows)
    One day's numbers: total, estimated, estimated share, target, remaining, net. log_food and
    get_log both use it. net_kcal only with add_back_exercise_kcal on: food minus each cardio
    entry's estimate, or its machine number when it has no estimate. macros, only when an entry of the day holds a
    macro or a daily macro goal is set: {protein, carbs, fat}, each {g: the sum over the entries that hold it, or
    null when none does, entries_without: the day's entries with it null, target_g: the daily goal (the user's
    protein_target_g, carbs_target_g, fat_target_g) or null, left_g: target_g minus g, g counted as 0 while null,
    below 0 when over, or null with no goal}. The summary's sentence a macro is said only while g is held.

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit), before any other read or work.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app import calllog, db, formatting, links, rules, users
from app.models import IMPORTED, CardioEntry, FoodEntry, SavedFood, User
from app.services import usda
from app.services.common import (
    DUPLICATE_MINUTES, check_by, check_data, check_read_limit, check_write_limit, dedupe_key, duplicate_note,
    entry_changed, entry_id, keep_version, lock_user, no_account, not_found, owned, truthy,
)

KIND = "food_entry"
ENTRIES = (1, 30)
NUMBER_AND_ID = "the app supplies the number for a saved or USDA food"
SOURCES_SHOWN = {"you_said": "you said", "saved_food": "saved food", "usda": "USDA", "claude_estimate": "estimate"}
ENTRY_FIELDS = ("description", "meal", "kcal", "estimated", "portion", "protein_g", "carbs_g", "fat_g",
                "macros_estimated", "saved_food_id", "servings", "usda_fdc_id", "grams")
MACRO_NAMES = {"protein_g": "protein", "carbs_g": "carbs", "fat_g": "fat"}  # a macro's column, and its key in macros
MACRO_WORDS = {"protein_g": "Protein", "carbs_g": "Carbs", "fat_g": "Fat"}
# A macro's column, and the user's daily goal for it in grams (macros-and-asking.md, decision 2 A).
MACRO_TARGETS = {"protein_g": "protein_target_g", "carbs_g": "carbs_target_g", "fat_g": "fat_target_g"}
PER_SERVING = tuple(f"{m}_per_serving" for m in rules.MACROS)  # a saved food's macros a serving
ATWATER = "atwater"  # the call log's key for the ratios of the macros' kcal to the kcal sent
ATWATER_KCAL = {"protein_g": 4, "carbs_g": 4, "fat_g": 9}  # kcal a gram
SAVE_FIELDS = ("name", "serving_label", "kcal_per_serving", "grams_per_serving", *PER_SERVING, "items")
# What a meal's item takes: a saved food, a USDA food, or the person's own number and macros.
ITEM_FIELDS = ("description", "kcal", "protein_g", "carbs_g", "fat_g", "saved_food_id", "servings", "usda_fdc_id",
               "grams")
MEAL_LABEL = "1 meal"  # a meal's serving_label when none is sent


def meal_for(now: datetime) -> str:
    """The meal slot for a local time: 04:00 to 11:00 breakfast, 11:00 to 16:00 lunch, 16:00 to 22:00
    dinner, 22:00 to 04:00 snack (so 00:30 is a late snack, not breakfast)."""
    if now.hour < 4:
        return "snack"
    if now.hour < 11:
        return "breakfast"
    if now.hour < 16:
        return "lunch"
    if now.hour < 22:
        return "dinner"
    return "snack"


def entry_dict(e: FoodEntry) -> dict:
    return {"id": e.id, "date": e.date.isoformat(), "meal": e.meal, "description": e.description,
            "kcal": int(e.kcal), "source": e.source, "estimated": e.estimated, "saved_food_id": e.saved_food_id,
            "servings": e.servings, "usda_fdc_id": e.usda_fdc_id, "grams": e.grams, "protein_g": e.protein_g,
            "carbs_g": e.carbs_g, "fat_g": e.fat_g, "macros_estimated": e.macros_estimated, "portion": e.portion}


def meal_items(f: SavedFood) -> list[dict] | None:
    """A saved meal's items as stored, or None for a plain food."""
    if f.items is None:
        return None
    try:
        items = json.loads(f.items)
    except ValueError:
        return None
    return items if isinstance(items, list) else None


def food_dict(f: SavedFood) -> dict:
    return {"id": f.id, "name": f.name, "serving_label": f.serving_label, "kcal_per_serving": f.kcal_per_serving,
            "grams_per_serving": f.grams_per_serving, **{column: getattr(f, column) for column in PER_SERVING},
            "items": meal_items(f)}


def saved_parts(e: FoodEntry) -> dict:
    """The saved food an entry came from, by name and serving, for showing it as "Roti × 2" with its
    serving (decision 14). Both null for an entry not logged from a saved food."""
    food = e.saved_food if e.saved_food_id is not None else None
    return {"saved_food_name": food.name if food else None, "serving_label": food.serving_label if food else None}


def entry_key(on, rows: list[tuple]) -> str:
    """What makes two food logs the same: the date and the whole set of entries, in any order."""
    return dedupe_key(on, sorted(rows, key=repr))


def entry_tuple(e) -> tuple:
    """The values of one entry that define it: meal, casefolded description, kcal, source and the ids."""
    get = e.get if isinstance(e, dict) else lambda k: getattr(e, k)
    return (get("meal"), get("description").casefold(), int(get("kcal")), get("source"), get("saved_food_id"),
            get("servings"), get("usda_fdc_id"), get("grams"))


def times(per: float | None, amount: float) -> float | None:
    """A macro a serving or a 100 g, times the amount, at 1 dp, worked in decimals so 0.3 x 1.5 is 0.45, kept as 0.5;
    null stays null."""
    if per is None:
        return None
    return rules.keep(float(Decimal(repr(float(per))) * Decimal(repr(float(amount)))), 1)


def day_macros(user, food_rows: list) -> dict | None:
    """The day's macros (FoodDay.macros), or None when no entry of the day holds one and no daily macro goal is set:
    so such a day answers as before. Each macro's g is the sum over the entries that hold it, at 1 dp, null when none
    does; target_g the person's daily goal or null; left_g the goal minus g (g as 0 while null), at 1 dp, or null with
    no goal."""
    held = {m: [getattr(e, m) for e in food_rows if getattr(e, m) is not None] for m in rules.MACROS}
    goals = {m: getattr(user, MACRO_TARGETS[m]) for m in rules.MACROS}
    if not any(held.values()) and all(goal is None for goal in goals.values()):
        return None
    out = {}
    for m, values in held.items():
        g, goal = (rules.keep(sum(values), 1) if values else None), goals[m]
        out[MACRO_NAMES[m]] = {"g": g, "target_g": goal,
                               "left_g": rules.keep(goal - (g or 0), 1) if goal is not None else None,
                               "entries_without": len(food_rows) - len(values)}
    return out


def day_numbers(user, food_rows: list, cardio_rows: list) -> dict:
    total = sum(int(e.kcal) for e in food_rows)
    estimated = sum(int(e.kcal) for e in food_rows if e.source == "claude_estimate")
    target = user.calorie_target_kcal
    net = None
    if user.add_back_exercise_kcal:
        burned = sum(int(c.estimated_kcal if c.estimated_kcal is not None else (c.machine_kcal or 0))
                     for c in cardio_rows)
        net = total - burned
    out = {"total_kcal": total, "estimated_kcal": estimated,
           "estimated_share_pct": round(100 * estimated / total) if total else 0,
           "target_kcal": target, "remaining_kcal": target - total if target is not None else None,
           "net_kcal": net}
    macros = day_macros(user, food_rows)
    if macros is not None:
        out["macros"] = macros
    return out


def _day_rows(s, user_id: int, on) -> tuple[list, list]:
    food = list(s.scalars(select(FoodEntry).where(FoodEntry.user_id == user_id, FoodEntry.date == on,
                                                  FoodEntry.deleted_at.is_(None)).order_by(FoodEntry.id)))
    cardio = list(s.scalars(select(CardioEntry).where(CardioEntry.user_id == user_id, CardioEntry.date == on,
                                                      CardioEntry.deleted_at.is_(None))))
    return food, cardio


def _check_entry(s, user_id: int, raw, where: str, default_meal: str,
                 resolved: dict) -> tuple[dict | None, list[dict]]:
    """One entry, checked: its stored values, or the errors. The tag is decided here."""
    if not isinstance(raw, dict):
        return None, [rules.error("invalid_field", f"{where}: an entry must be an object of named fields.",
                                  "Send each entry with description and either kcal, saved_food_id or usda_fdc_id.")]
    errors = [rules.error("invalid_field", f"{where}: '{rules.quote(k)}' is not a field of a food entry.",
                          f"Use: {', '.join(ENTRY_FIELDS)}. source is decided by the server.")
              for k in raw if k not in ENTRY_FIELDS]
    present = lambda k: raw.get(k) not in (None, "")  # noqa: E731
    out = {"saved_food_id": None, "servings": None, "usda_fdc_id": None, "grams": None, "estimated": False,
           **dict.fromkeys(rules.MACROS), "macros_estimated": None}
    if present("meal"):
        out["meal"], err = rules.match_meal(raw["meal"], where)
        errors += [err] if err else []
    else:
        out["meal"] = default_meal
    description = None
    if present("description") or not (present("saved_food_id") or present("usda_fdc_id")):
        description, err = rules.required_text(raw.get("description"), "description", where)
        errors += [err] if err else []
    estimated = truthy(raw.get("estimated"))
    ids = [k for k in ("saved_food_id", "usda_fdc_id") if present(k)]
    if ids and present("kcal"):
        errors.append(rules.error("invalid_field", f"{where}: kcal was sent with {ids[0]}.",
                                  f"Leave kcal out: {NUMBER_AND_ID}."))
    if len(ids) == 2:
        errors.append(rules.error("invalid_field", f"{where}: both saved_food_id and usda_fdc_id were sent.",
                                  "Send one of them."))
    if ids and estimated:
        errors.append(rules.error("invalid_field", f"{where}: estimated was sent with {ids[0]}.",
                                  f"Leave estimated out: {NUMBER_AND_ID}."))
    macros = {}
    for macro in (*rules.MACROS, "macros_estimated"):
        if ids and present(macro):
            errors.append(rules.error("invalid_field", f"{where}: {macro} was sent with {ids[0]}.",
                                      f"Leave {macro} out: {NUMBER_AND_ID}."))
        elif present(macro) and macro != "macros_estimated":
            macros[macro], err = rules.bounded(macro, raw[macro], where)
            errors += [err] if err else []
    portion, err = rules.portion(raw.get("portion"), where)  # nothing, a blank or invisible characters: no portion
    refused = err is not None
    errors += [err] if err else []
    if estimated and not ids and portion is None and not refused:  # decision 6: ask the person, never guess
        errors.append(rules.portion_missing(where))
    for wrong, right in (("grams", "saved_food_id"), ("servings", "usda_fdc_id")):
        if present(wrong) and right in ids:
            errors.append(rules.error("invalid_field", f"{where}: {wrong} does not go with {right}.",
                                      "A saved food takes servings; a USDA food takes grams."))
    # An amount with no food to measure it by is refused, never dropped (inputs.md, the fix round).
    for amount, food_id, food in (("servings", "saved_food_id", "a saved food's"), ("grams", "usda_fdc_id",
                                                                                     "a USDA food's")):
        if present(amount) and not ids:
            errors.append(rules.error("invalid_field", f"{where}: {amount} goes only with {food_id}.",
                                      f"Send {amount} with {food} id, or leave it out and send kcal for the amount "
                                      "eaten."))
    if errors:
        return None, errors

    if "saved_food_id" in ids:
        food = owned(s, SavedFood, user_id, raw["saved_food_id"])
        if food is None:
            return None, [{**not_found("saved_food"), "message": f"{where}: " + not_found("saved_food")["message"]}]
        servings, err = rules.bounded("servings", raw.get("servings", 1), where)
        if err:
            return None, [err]
        what = f"{formatting.show(servings, 'servings')} servings of {food.name}"
        kcal, err = _worked(food.kcal_per_serving * servings, where, what, "Give fewer servings")
        worked, errs = _worked_macros({m: times(getattr(food, f"{m}_per_serving"), servings) for m in rules.MACROS},
                                      where, what, "Give fewer servings")
        if err or errs:
            return None, ([err] if err else []) + errs
        out.update(source="saved_food", saved_food_id=food.id, servings=servings, kcal=kcal,
                   description=description or food.name, **worked)
    elif "usda_fdc_id" in ids:
        fdc_id = entry_id(raw["usda_fdc_id"])
        if fdc_id is None:
            return None, [rules.error("invalid_field", f"{where}: usda_fdc_id '{rules.quote(raw['usda_fdc_id'])}' "
                                      "is not an id.", "Use an fdc_id from find_food.")]
        grams, err = rules.bounded("grams", raw.get("grams"), where)
        if err:
            return None, [err]
        found, err = resolved.get(fdc_id, (None, usda.unavailable()))
        if err:
            return None, [err]
        what = f"{formatting.show(grams, 'grams')} of {found['description']}"
        kcal, err = _worked(found["kcal_per_100g"] * grams / 100, where, what, "Give fewer grams")
        worked, errs = _worked_macros({m: times(found.get(f"{m}_per_100g"), grams / 100) for m in rules.MACROS},
                                      where, what, "Give fewer grams")
        if err or errs:
            return None, ([err] if err else []) + errs
        out.update(source="usda", usda_fdc_id=fdc_id, grams=grams, kcal=kcal,
                   description=description or found["description"][:rules.TEXT_LIMITS["description"]], **worked)
    else:
        kcal, err = rules.bounded("kcal", raw.get("kcal"), where)
        if err:
            return None, [err]
        out.update(source="claude_estimate" if estimated else "you_said", estimated=estimated, kcal=kcal,
                   description=description, **macros)
        if macros:  # left out, the mark is true: a slip shows as an estimate, never as the person's own number
            out["macros_estimated"] = truthy(raw["macros_estimated"]) if present("macros_estimated") else True
    out["portion"] = portion
    return out, []


def _worked(value: float, where: str, what: str, fewer: str, field: str = "kcal") -> tuple[int | None, dict | None]:
    """A kcal (or a macro) the app worked out, held to the same range a stated number is."""
    number, err = rules.bounded(field, value, where)
    if err is None:
        return number, None
    lo, hi, _ = rules.NUMBERS[field]
    quantity = rules.NUMBER_QUANTITIES[field]
    unit = "" if field == "kcal" else f" of {MACRO_NAMES[field]}"
    return None, rules.error("invalid_field", f"{where}: {rules.quote(what)} works out at "
                             f"{formatting.show(value, quantity)}{unit}, outside {rules.span(lo, hi, quantity)} "
                             "for one entry.", f"{fewer}: the app supplies the number for a saved or USDA food.")


def _worked_macros(values: dict, where: str, what: str, fewer: str) -> tuple[dict, list[dict]]:
    """A saved or USDA food's macros for the amount, each held to its range ({} of errors when they fit), and its
    mark: false when it holds any, else null."""
    out, errors = {}, []
    for m, value in values.items():
        if value is None:
            out[m] = None
            continue
        out[m], err = _worked(value, where, what, fewer, m)
        errors += [err] if err else []
    out["macros_estimated"] = False if any(out[m] is not None for m in rules.MACROS) else None
    return out, errors


def _usda_ids(raw) -> list[int]:
    """The USDA ids log_food will need, read from the entries before anything is locked."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("usda_fdc_id") not in (None, "") \
                and item.get("kcal") in (None, "") and item.get("saved_food_id") in (None, ""):
            fdc_id = entry_id(item["usda_fdc_id"])
            if fdc_id is not None and fdc_id not in out:
                out.append(fdc_id)
    return out


def _summary(on, today, rows: list[FoodEntry], day: dict) -> str:
    """'Tue 10 Nov. Lunch: Roti, 240 kcal, saved food. Day total 2,430 kcal, 980 kcal of it estimated.
    430 kcal over the 2,000 kcal target.' One sentence an entry, its tag in its own slot."""
    kcal = lambda n: formatting.show(n, "kcal")  # noqa: E731
    text = f"{formatting.day(on, today)}." + "".join(
        f" {e.meal.capitalize()}: {e.description}, {kcal(e.kcal)}, {SOURCES_SHOWN[e.source]}." for e in rows)
    text += f" Day total {kcal(day['total_kcal'])}"
    if day["estimated_kcal"]:
        text += f", {kcal(day['estimated_kcal'])} of it estimated"
    text += "."
    if day["remaining_kcal"] is not None:
        left, target = formatting.show(day["remaining_kcal"], "kcal_left"), kcal(day["target_kcal"])
        text += f" {left} {'the' if day['remaining_kcal'] < 0 else 'of the'} {target} target."
    return text + macros_text(day.get("macros"))


def macros_text(macros: dict | None) -> str:
    """The day's macros in words, one sentence a macro that an entry holds: " Protein 92 g so far, 1 entry with none,
    58 g left of the 150 g goal." "so far" only while an entry of the day has none; the goal's part only with a goal
    set, worded as the calorie target's is: "12 g over the 150 g goal." when over. "" for a day with no macro."""
    out = ""
    for m, word in MACRO_WORDS.items():
        held = (macros or {}).get(MACRO_NAMES[m])
        if not held or held["g"] is None:
            continue
        out += f" {word} {formatting.show(held['g'], 'grams')}"
        n = held["entries_without"]
        if n:
            out += f" so far, {formatting.show(n, 'count')} {'entry' if n == 1 else 'entries'} with none"
        if held.get("left_g") is not None:
            left, goal = held["left_g"], formatting.show(held["target_g"], "grams")
            out += (f", {formatting.show(-left, 'grams')} over the {goal} goal" if left < 0
                    else f", {formatting.show(left, 'grams')} left of the {goal} goal")
        out += "."
    return out


def atwater_items(checked: list[dict]) -> list[str]:
    """The call log's atwater ratios: each entry that sent kcal above 0 and all three macros, its 4 x protein + 4 x
    carbs + 9 x fat over its kcal, at 2 dp. No value of the food itself."""
    items = []
    for entry in checked:
        if entry["source"] not in ("you_said", "claude_estimate") or not entry["kcal"]:
            continue
        if all(entry[m] is not None for m in rules.MACROS):
            kcal_of = sum(ATWATER_KCAL[m] * entry[m] for m in rules.MACROS)
            items.append(str(formatting.shown_number(kcal_of / entry["kcal"], "rate")))
    return items


def note_atwater(items: list[str]) -> None:
    """atwater= on the call's row, cut at a whole item to the room the notes column has left (calllog.room), as the
    per-hand check's pair= is. Nothing when no entry has a ratio or none fits."""
    kept: list[str] = []
    for item in items:
        if len(",".join([*kept, item])) > calllog.room(ATWATER):
            break
        kept.append(item)
    if kept:
        calllog.note(ATWATER, ",".join(kept))


def log_food(user_id: int, data: dict | None, by: str) -> dict:
    """Log one or more food entries. Returns {kind: "food_entry", ids, duplicate, entries, day, summary,
    links {day, foods}[, note]} or {error, message, fix}. See the module notes."""
    data, err = check_data(data)
    err = err or check_by(by)
    if err:
        return err
    # USDA first, outside any transaction: a lookup is a network call and may write the cache,
    # and neither may happen while this person's row is locked. A caller already at the write limit
    # is refused before any lookup (an unlocked count; the locked check below is the real one), so a
    # throttled loop never spends the USDA quota.
    wanted = _usda_ids(data.get("entries"))[:ENTRIES[1]]
    if wanted:
        with db.new_session() as s:
            limited = check_write_limit(s, user_id)
        if limited:
            return limited
    resolved = {}
    for fdc_id in wanted:  # the first that fails ends them: the call is refused on it; an id left out is unavailable
        resolved[fdc_id] = usda.food(fdc_id)
        if resolved[fdc_id][1] is not None:
            break
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        today = users.today_for(user)
        errors = []
        on, err = rules.entry_date(data.get("date"), today, "meals")
        errors += [err] if err else []
        raw = data.get("entries")
        lo, hi = ENTRIES
        if not isinstance(raw, list) or not lo <= len(raw) <= hi:
            count = len(raw) if isinstance(raw, list) else 0
            errors.append(rules.error("invalid_field", f"entries holds {count}; it needs {lo} to {hi}.",
                                      "Send entries as a list: one object per food, each with a description."))
            raw = raw[:hi] if isinstance(raw, list) else []
        default_meal = meal_for(users.now_for(user))
        checked = []
        for n, item in enumerate(raw, start=1):
            entry, errs = _check_entry(s, user.id, item, f"Entry {n}", default_meal, resolved)
            errors += errs
            checked.append(entry)
        if errors:
            return rules.combine(errors)
        note_atwater(atwater_items(checked))

        key = entry_key(on, [entry_tuple(e) for e in checked])
        rows = None
        if not truthy(data.get("allow_repeat")):
            rows = _first_set(s, user.id, on, key, len(checked))
        duplicate = rows is not None
        if not duplicate:
            rows = [FoodEntry(user_id=user.id, date=on, meal=e["meal"], description=e["description"], kcal=e["kcal"],
                              source=e["source"], estimated=e["estimated"], saved_food_id=e["saved_food_id"],
                              servings=e["servings"], usda_fdc_id=e["usda_fdc_id"], grams=e["grams"],
                              protein_g=e["protein_g"], carbs_g=e["carbs_g"], fat_g=e["fat_g"],
                              macros_estimated=e["macros_estimated"], portion=e["portion"],
                              logged_by=by, dedupe_key=key) for e in checked]
            s.add_all(rows)
            s.flush()
        day = day_numbers(user, *_day_rows(s, user.id, on))
        out = {"kind": KIND, "ids": [r.id for r in rows], "duplicate": duplicate,
               "entries": [entry_dict(r) for r in rows], "day": day,
               "summary": ("Already logged: " if duplicate else "") + _summary(on, today, rows, day),
               "links": {"day": links.page("day", date=on), "foods": links.page("foods")}}
        guesses = sum(1 for r in rows if r.source == "claude_estimate")
        if duplicate:
            out["note"] = duplicate_note("meal")
        elif guesses:
            out["note"] = (f"{formatting.show(guesses, 'count')} {'entry is' if guesses == 1 else 'entries are'} Claude's "
                           f"{'estimate' if guesses == 1 else 'estimates'}: the person can confirm or correct "
                           f"{'it' if guesses == 1 else 'them'}, and the day total says how much of it is estimated.")
        return out


def _first_set(s, user_id: int, on, key: str, size: int) -> list | None:
    """The live entries of the first set logged with this key in the last 10 minutes, or None."""
    since = db.utcnow() - timedelta(minutes=DUPLICATE_MINUTES)
    rows = list(s.scalars(select(FoodEntry).where(
        FoodEntry.user_id == user_id, FoodEntry.date == on, FoodEntry.dedupe_key == key,
        FoodEntry.deleted_at.is_(None), FoodEntry.created_at >= since).order_by(FoodEntry.id).limit(size)))
    return rows if len(rows) == size else None


# ---------------------------------------------------------------- saved foods

def save_food(user_id: int, data: dict | None, by: str, drawn_at=None) -> dict:
    """Keep a food for logging by id. Returns {kind: "saved_food", id, created, food, summary, links {foods}}
    or {error, message, fix}."""
    data, err = check_data(data)
    err = err or check_by(by)
    if err:
        return err
    meal = data.get("items") is not None
    # A meal's USDA items are looked up before the lock, as log_food's are: no network call under it.
    wanted = _usda_ids(data.get("items"))[:rules.MEAL_ITEMS[1]] if meal else []
    if wanted:
        with db.new_session() as s:
            limited = check_write_limit(s, user_id)
        if limited:
            return limited
    resolved = {}
    for fdc_id in wanted:
        resolved[fdc_id] = usda.food(fdc_id)
        if resolved[fdc_id][1] is not None:
            break
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        errors, values = [], {}
        values["name"], err = rules.required_text(data.get("name"), "name")
        errors += [err] if err else []
        if meal and data.get("serving_label") in (None, ""):
            values["serving_label"] = MEAL_LABEL
        else:
            values["serving_label"], err = rules.required_text(data.get("serving_label"), "serving_label")
            errors += [err] if err else []
        if meal:
            sent = [f for f in ("kcal_per_serving", *PER_SERVING) if data.get(f) not in (None, "")]
            errors += [rules.error("invalid_field", f"{f} was sent with items.",
                                   f"Leave {f} out: Spotter adds up a meal's numbers from its items.") for f in sent]
            items, errs = _meal_items(s, user.id, data["items"], resolved)
            errors += errs
            if items is not None and not errs:
                sums, errs = _meal_sums(items)
                errors += errs
                values.update(sums, items=json.dumps(items))
        else:
            values["kcal_per_serving"], err = rules.bounded("kcal_per_serving", data.get("kcal_per_serving"))
            errors += [err] if err else []
            for field in (f for f in PER_SERVING if f in data):  # left out: a saved one is kept, a new food has none
                values[field] = None
                if data[field] not in (None, ""):
                    values[field], err = rules.bounded(field, data[field])
                    errors += [err] if err else []
            values["items"] = None
        if meal or "grams_per_serving" in data:  # left out of a plain save: a saved one is kept, a new food has none
            values["grams_per_serving"] = None
            if data.get("grams_per_serving") not in (None, ""):
                values["grams_per_serving"], err = rules.bounded("grams_per_serving", data["grams_per_serving"])
                errors += [err] if err else []
        unknown = [k for k in data if k not in SAVE_FIELDS]
        errors += [rules.error("invalid_field", f"'{rules.quote(k)}' is not a field of a saved food.",
                               f"Use: {', '.join(SAVE_FIELDS)}.") for k in unknown]
        if errors:
            return rules.combine(errors)
        name_key = " ".join(values["name"].split()).casefold()
        food = s.scalar(select(SavedFood).where(SavedFood.user_id == user.id, SavedFood.name_key == name_key))
        created = food is None
        if created:
            food = SavedFood(user_id=user.id, name_key=name_key)
            s.add(food)
        else:
            stale = entry_changed(food, drawn_at)
            if stale:
                return stale
        was_deleted = not created and food.deleted_at is not None
        if not meal and not created and food.items is not None and not was_deleted:
            return meal_name_taken(food.name)  # a plain save would drop the meal's foods
        if not meal and was_deleted:  # one brought back plain starts from what is sent, never a meal's sums
            values.update({field: None for field in (*PER_SERVING, "grams_per_serving") if field not in values})
        differs = created or any(getattr(food, field) != value for field, value in values.items())
        if differs and not created:
            keep_version(s, food, by)  # the food as it stands, before it changes
        if differs or was_deleted:  # a save that changes nothing writes nothing, and updated_at stays
            for field, value in values.items():
                setattr(food, field, value)
            food.deleted_at = None
            food.updated_at = db.utcnow()
            s.flush()
        grams = f", {formatting.show(food.grams_per_serving, 'grams')} a serving" if food.grams_per_serving else ""
        verb = "Saved" if created else ("Brought back and updated" if was_deleted else "Updated")
        items = meal_items(food)
        foods = f", from {formatting.show(len(items), 'count')} foods" if items else ""
        return {"kind": "saved_food", "id": food.id, "created": created, "food": food_dict(food),
                "summary": (f"{verb} {food.name}: {food.serving_label} is "
                            f"{formatting.show(food.kcal_per_serving, 'kcal_per_serving')}{grams}{foods}."
                            + serving_macros_text(food)),
                "links": {"foods": links.page("foods")}}


def meal_name_taken(name: str) -> dict:
    """A plain save of a live saved meal's name: refused, so the meal keeps its foods (the web's MEAL_NAME_TAKEN)."""
    return rules.error("invalid_field", f"name '{rules.quote(name)}' is a saved meal.",
                       "Send the meal's foods as items to change it, or give another name. Nothing was saved.")


def serving_macros_text(f: SavedFood) -> str:
    """A saved food's macros a serving in words: " Protein 3 g, carbs 18 g and fat 0.4 g a serving." "" with none."""
    held = [f"{MACRO_NAMES[m]} {formatting.show(getattr(f, f'{m}_per_serving'), 'grams')}" for m in rules.MACROS
            if getattr(f, f"{m}_per_serving") is not None]
    if not held:
        return ""
    words = held[0] if len(held) == 1 else ", ".join(held[:-1]) + " and " + held[-1]
    return f" {words[:1].upper()}{words[1:]} a serving."


def _meal_items(s, user_id: int, raw, resolved: dict) -> tuple[list[dict] | None, list[dict]]:
    """A meal's items, each checked as a food entry is (_check_entry) and kept as resolved: {description, kcal,
    protein_g, carbs_g, fat_g, source, saved_food_id, servings, usda_fdc_id, grams}. Or the errors, every one at once.
    An estimate, or a saved food that is a meal itself, is refused (decision 10)."""
    lo, hi = rules.MEAL_ITEMS
    if not isinstance(raw, list) or not lo <= len(raw) <= hi:
        count = len(raw) if isinstance(raw, list) else 0
        return None, [rules.error("invalid_field", f"items holds {count}; a meal needs {lo} to {hi}.",
                                  f"Send items as a list: one object per food of the meal, {lo} to {hi}.")]
    items, errors = [], []
    for n, item in enumerate(raw, start=1):
        where = f"Item {n}"
        if not isinstance(item, dict):
            errors.append(rules.error("invalid_field", f"{where}: an item must be an object of named fields.",
                                      "Send each item with saved_food_id, usda_fdc_id, or description and kcal."))
            continue
        if truthy(item.get("estimated")):
            errors.append(rules.meal_item_refused(where, "estimated"))
            continue
        unknown = [k for k in item if k not in ITEM_FIELDS and k != "estimated"]
        if unknown:
            errors += [rules.error("invalid_field", f"{where}: '{rules.quote(k)}' is not a field of a meal's item.",
                                   f"Use: {', '.join(ITEM_FIELDS)}.") for k in unknown]
            continue
        entry, errs = _check_entry(s, user_id, {k: v for k, v in item.items() if k != "estimated"}, where, "lunch",
                                   resolved)
        if errs:
            errors += errs
            continue
        if entry["saved_food_id"] is not None and s.get(SavedFood, entry["saved_food_id"]).items is not None:
            errors.append(rules.meal_item_refused(where, "meal"))
            continue
        items.append({k: entry[k] for k in ("description", "kcal", *rules.MACROS, "source", "saved_food_id",
                                            "servings", "usda_fdc_id", "grams")})
    return (None if errors else items), errors


def _meal_sums(items: list[dict]) -> tuple[dict, list[dict]]:
    """A meal's kcal and macros a serving: the sums of its items, a macro null when any item lacks it, each held to
    the range one sent for a saved food is."""
    totals = {"kcal_per_serving": sum(i["kcal"] for i in items),
              **{f"{m}_per_serving": (None if any(i[m] is None for i in items) else sum(i[m] for i in items))
                 for m in rules.MACROS}}
    out, errors = {}, []
    for field, value in totals.items():
        if value is None:
            out[field] = None
            continue
        out[field], err = rules.bounded(field, value)
        if err:
            lo, hi, _ = rules.NUMBERS[field]
            quantity = rules.NUMBER_QUANTITIES[field]
            errors.append(rules.error("invalid_field", f"The meal's {field} works out at "
                                      f"{formatting.show(value, quantity)}, outside {rules.span(lo, hi, quantity)}.",
                                      "Save fewer foods or smaller amounts in the meal."))
    return out, errors


# ---------------------------------------------------------------- finding a food

def find_food(user_id: int, query, limit=8) -> dict:
    """Saved and recent first, then USDA. Returns {query, saved, recent, usda, usda_note, summary} or an error."""
    text, err = rules.text(query, "query", 100)
    if err:
        return err
    if not text:
        return rules.error("missing_field", "query is missing.", "Give the food's name, such as banana.")
    count, err = _limit(limit)
    if err:
        return err
    needle = " ".join(text.split()).casefold()
    with db.new_session() as s:
        if s.get(User, user_id) is None:
            return no_account()
        limited = check_read_limit(s, user_id, "find_food")
        if limited:
            return limited
        used = _usage(s, user_id)
        foods = [f for f in s.scalars(select(SavedFood).where(SavedFood.user_id == user_id,
                                                              SavedFood.deleted_at.is_(None)))
                 if needle in f.name_key]
        foods.sort(key=lambda f: (-used.get(f.id, (0, None))[0], f.name_key))
        saved = [{**food_dict(f), "times_logged": used.get(f.id, (0, None))[0],
                  "last_logged": used[f.id][1].isoformat() if f.id in used else None} for f in foods[:count]]

        groups: dict[str, dict] = {}
        # An imported entry is the other app's record, often a meal's or a day's total: never offered back.
        for e in s.scalars(select(FoodEntry).where(
                FoodEntry.user_id == user_id, FoodEntry.deleted_at.is_(None), FoodEntry.logged_by != IMPORTED,
                FoodEntry.source.in_(("you_said", "claude_estimate"))).order_by(FoodEntry.date, FoodEntry.id)):
            folded = " ".join(e.description.split()).casefold()
            if needle not in folded:
                continue
            g = groups.setdefault(folded, {"times": 0})
            g.update(description=e.description, kcal=int(e.kcal), source=e.source, last_logged=e.date.isoformat())
            g["times"] += 1
        # Most frequent first; between equals, the one logged most recently.
        recent = sorted(sorted(groups.values(), key=lambda g: g["last_logged"], reverse=True),
                        key=lambda g: -g["times"])[:count]
        recent = [{k: g[k] for k in ("description", "kcal", "source", "last_logged", "times")} for g in recent]

    found, err = usda.search(text)
    if err is None:
        found, usda_note = found[:count], None
    else:
        found = None
        usda_note = ("USDA lookup is off: no key is set." if err["error"] == "usda_off"
                     else err["message"].removesuffix(".") + ": use a saved food or the person's own number.")
    count = lambda n: formatting.show(n, "count")  # noqa: E731
    summary = f"For '{rules.quote(text)}': {count(len(saved))} saved, {count(len(recent))} recent"
    summary += f", {count(len(found))} from USDA." if found is not None else ". " + usda_note
    return {"query": text, "saved": saved, "recent": recent, "usda": found, "usda_note": usda_note,
            "summary": summary}


def _usage(s, user_id: int) -> dict[int, tuple[int, object]]:
    """{saved food id: (times logged, last date logged)} over the person's live entries. find_food and
    list_saved_foods both read it, so the Foods page and Claude see the same counts."""
    return {fid: (n, last) for fid, n, last in s.execute(
        select(FoodEntry.saved_food_id, func.count(), func.max(FoodEntry.date)).where(
            FoodEntry.user_id == user_id, FoodEntry.deleted_at.is_(None), FoodEntry.saved_food_id.is_not(None))
        .group_by(FoodEntry.saved_food_id))}


def list_saved_foods(user_id: int) -> dict:
    """Every saved food the person has, for the Foods page: live ones first by name, then the deleted.

    Returns {foods [{id, name, serving_label, kcal_per_serving, grams_per_serving, times_logged,
    last_logged, deleted_at}], summary} or {error, message, fix}. A read: no lock."""
    with db.new_session() as s:
        if s.get(User, user_id) is None:
            return no_account()
        used = _usage(s, user_id)
        rows = sorted(s.scalars(select(SavedFood).where(SavedFood.user_id == user_id)),
                      key=lambda f: (f.deleted_at is not None, f.name_key, f.id))
        foods = [{**food_dict(f), "times_logged": used.get(f.id, (0, None))[0],
                  "last_logged": used[f.id][1].isoformat() if f.id in used else None,
                  "deleted_at": f.deleted_at.isoformat() if f.deleted_at else None} for f in rows]
    live = sum(1 for f in foods if f["deleted_at"] is None)
    summary = f"{formatting.show(live, 'count')} saved food{'s' if live != 1 else ''}"
    summary += f", {formatting.show(len(foods) - live, 'count')} deleted." if len(foods) > live else "."
    return {"foods": foods, "summary": summary}


def _limit(value) -> tuple[int | None, dict | None]:
    if value is None:
        return 8, None
    number = rules.to_float(value)
    if number is None or number != int(number) or not 1 <= number <= 25:
        return None, rules.error("invalid_field", f"limit '{rules.quote(value)}' is not a whole number from 1 to 25.",
                                 "Give limit from 1 to 25, or leave it out for 8.")
    return int(number), None
