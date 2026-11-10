"""Food: what a person ate, the foods they keep, and finding a food to log.

log_food(user_id, data, by)
    data: entries [{description, meal?, kcal?, estimated?, saved_food_id?, servings?, usda_fdc_id?, grams?}]
    (1 to 30), date? (one date for the call: blank = the person's today; never after today; at most
    two years back), allow_repeat?
    - The tag is the server's (rule 13): saved_food when a saved_food_id is given, usda when a
      usda_fdc_id is given, claude_estimate when estimated is true, else you_said. source is never
      an input.
    - A number and an id never travel together (rule 14): kcal with either id is invalid_field. A
      saved food is logged by id and servings (default 1); a USDA food by id and grams. The app works
      out kcal, stored as a whole number and held to the same 0 to 5,000 a stated number is.
    - USDA ids are looked up before the user row is locked: no network call under the lock.
    - A meal slot not given comes from the person's clock (users.now_for): 04:00 to 11:00 breakfast,
      11:00 to 16:00 lunch, 16:00 to 22:00 dinner, 22:00 to 04:00 snack.
    - All or nothing: every entry is checked first, every problem comes back at once.
    - The duplicate guard compares the whole set, as log_lifts does: the same date and the same
      entries within 10 minutes return the first set with duplicate true.

save_food(user_id, data, by)
    data: name, serving_label, kcal_per_serving, grams_per_serving?. The same name (casefolded)
    updates the saved food; a deleted one comes back live.

find_food(user_id, query, limit=8)
    A read. The person's saved foods whose name holds the query, most logged first, then by name;
    their own you_said and claude_estimate entries whose description holds it, grouped by the
    casefolded description, most frequent first; then USDA when the key is set.

day_numbers(user, food_rows, cardio_rows)
    One day's numbers: total, estimated, estimated share, target, remaining, net. log_food and
    get_log both use it. net_kcal only with add_back_exercise_kcal on: food minus each cardio
    entry's estimate, or its machine number when it has no estimate.

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit), before any other read or work.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app import db, links, rules, users
from app.models import CardioEntry, FoodEntry, SavedFood, User
from app.services import usda
from app.services.common import (
    DUPLICATE_MINUTES, check_by, check_data, check_write_limit, dedupe_key, duplicate_note, entry_id, lock_user,
    no_account, not_found, owned, truthy,
)

KIND = "food_entry"
ENTRIES = (1, 30)
NUMBER_AND_ID = "the app supplies the number for a saved or USDA food"
SOURCES_SHOWN = {"you_said": "you said", "saved_food": "saved food", "usda": "USDA", "claude_estimate": "estimate"}
ENTRY_FIELDS = ("description", "meal", "kcal", "estimated", "saved_food_id", "servings", "usda_fdc_id", "grams")


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
            "servings": e.servings, "usda_fdc_id": e.usda_fdc_id, "grams": e.grams}


def food_dict(f: SavedFood) -> dict:
    return {"id": f.id, "name": f.name, "serving_label": f.serving_label, "kcal_per_serving": f.kcal_per_serving,
            "grams_per_serving": f.grams_per_serving}


def entry_key(on, rows: list[tuple]) -> str:
    """What makes two food logs the same: the date and the whole set of entries, in any order."""
    return dedupe_key(on, sorted(rows, key=repr))


def entry_tuple(e) -> tuple:
    """The values of one entry that define it: meal, casefolded description, kcal, source and the ids."""
    get = e.get if isinstance(e, dict) else lambda k: getattr(e, k)
    return (get("meal"), get("description").casefold(), int(get("kcal")), get("source"), get("saved_food_id"),
            get("servings"), get("usda_fdc_id"), get("grams"))


def day_numbers(user, food_rows: list, cardio_rows: list) -> dict:
    total = sum(int(e.kcal) for e in food_rows)
    estimated = sum(int(e.kcal) for e in food_rows if e.source == "claude_estimate")
    target = user.calorie_target_kcal
    net = None
    if user.add_back_exercise_kcal:
        burned = sum(int(c.estimated_kcal if c.estimated_kcal is not None else (c.machine_kcal or 0))
                     for c in cardio_rows)
        net = total - burned
    return {"total_kcal": total, "estimated_kcal": estimated,
            "estimated_share_pct": round(100 * estimated / total) if total else 0,
            "target_kcal": target, "remaining_kcal": target - total if target is not None else None,
            "net_kcal": net}


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
    out = {"saved_food_id": None, "servings": None, "usda_fdc_id": None, "grams": None, "estimated": False}
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
    for wrong, right in (("grams", "saved_food_id"), ("servings", "usda_fdc_id")):
        if present(wrong) and right in ids:
            errors.append(rules.error("invalid_field", f"{where}: {wrong} does not go with {right}.",
                                      "A saved food takes servings; a USDA food takes grams."))
    if errors:
        return None, errors

    if "saved_food_id" in ids:
        food = owned(s, SavedFood, user_id, raw["saved_food_id"])
        if food is None:
            return None, [{**not_found("saved_food"), "message": f"{where}: " + not_found("saved_food")["message"]}]
        servings, err = rules.bounded("servings", raw.get("servings", 1), where)
        if err:
            return None, [err]
        kcal, err = _worked(food.kcal_per_serving * servings, where,
                            f"{servings:g} servings of {food.name}", "Give fewer servings")
        if err:
            return None, [err]
        out.update(source="saved_food", saved_food_id=food.id, servings=servings, kcal=kcal,
                   description=description or food.name)
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
        kcal, err = _worked(found["kcal_per_100g"] * grams / 100, where, f"{grams:g} g of {found['description']}",
                            "Give fewer grams")
        if err:
            return None, [err]
        out.update(source="usda", usda_fdc_id=fdc_id, grams=grams, kcal=kcal,
                   description=description or found["description"][:rules.TEXT_LIMITS["description"]])
    else:
        kcal, err = rules.bounded("kcal", raw.get("kcal"), where)
        if err:
            return None, [err]
        out.update(source="claude_estimate" if estimated else "you_said", estimated=estimated, kcal=kcal,
                   description=description)
    return out, []


def _worked(value: float, where: str, what: str, fewer: str) -> tuple[int | None, dict | None]:
    """A kcal the app worked out, held to the same 0 to 5,000 a stated number is."""
    kcal, err = rules.bounded("kcal", value, where)
    if err is None:
        return kcal, None
    lo, hi, _ = rules.NUMBERS["kcal"]
    return None, rules.error("invalid_field", f"{where}: {rules.quote(what)} works out at {round(value):,} kcal, "
                             f"outside {lo:,} to {hi:,} for one entry.",
                             f"{fewer}: the app supplies the number for a saved or USDA food.")


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


def _summary(on, rows: list[FoodEntry], day: dict) -> str:
    by_meal: dict[str, list[str]] = {}
    for e in rows:
        by_meal.setdefault(e.meal, []).append(f"{e.description} {int(e.kcal)} kcal ({SOURCES_SHOWN[e.source]})")
    parts = [f"{meal.capitalize()}: {', '.join(items)}" for meal, items in by_meal.items()]
    text = f"{on.isoformat()}: " + "; ".join(parts) + f". Day total {day['total_kcal']} kcal"
    if day["estimated_kcal"]:
        text += f", {day['estimated_kcal']} of it estimated"
    if day["remaining_kcal"] is not None:
        text += f"; {day['remaining_kcal']} kcal left of {day['target_kcal']}"
    return text + "."


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
    resolved = {fdc_id: usda.food(fdc_id) for fdc_id in wanted}
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

        key = entry_key(on, [entry_tuple(e) for e in checked])
        rows = None
        if not truthy(data.get("allow_repeat")):
            rows = _first_set(s, user.id, on, key, len(checked))
        duplicate = rows is not None
        if not duplicate:
            rows = [FoodEntry(user_id=user.id, date=on, meal=e["meal"], description=e["description"], kcal=e["kcal"],
                              source=e["source"], estimated=e["estimated"], saved_food_id=e["saved_food_id"],
                              servings=e["servings"], usda_fdc_id=e["usda_fdc_id"], grams=e["grams"],
                              logged_by=by, dedupe_key=key) for e in checked]
            s.add_all(rows)
            s.flush()
        day = day_numbers(user, *_day_rows(s, user.id, on))
        out = {"kind": KIND, "ids": [r.id for r in rows], "duplicate": duplicate,
               "entries": [entry_dict(r) for r in rows], "day": day,
               "summary": ("Already logged: " if duplicate else "") + _summary(on, rows, day),
               "links": {"day": links.page("day", date=on), "foods": links.page("foods")}}
        guesses = sum(1 for r in rows if r.source == "claude_estimate")
        if duplicate:
            out["note"] = duplicate_note("meal")
        elif guesses:
            out["note"] = (f"{guesses} {'entry is' if guesses == 1 else 'entries are'} Claude's "
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

def save_food(user_id: int, data: dict | None, by: str) -> dict:
    """Keep a food for logging by id. Returns {kind: "saved_food", id, created, food, summary, links {foods}}
    or {error, message, fix}."""
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
        errors, values = [], {}
        for field in ("name", "serving_label"):
            values[field], err = rules.required_text(data.get(field), field)
            errors += [err] if err else []
        values["kcal_per_serving"], err = rules.bounded("kcal_per_serving", data.get("kcal_per_serving"))
        errors += [err] if err else []
        values["grams_per_serving"] = None
        if data.get("grams_per_serving") not in (None, ""):
            values["grams_per_serving"], err = rules.bounded("grams_per_serving", data["grams_per_serving"])
            errors += [err] if err else []
        unknown = [k for k in data if k not in ("name", "serving_label", "kcal_per_serving", "grams_per_serving")]
        errors += [rules.error("invalid_field", f"'{rules.quote(k)}' is not a field of a saved food.",
                               "Use: name, serving_label, kcal_per_serving, grams_per_serving.") for k in unknown]
        if errors:
            return rules.combine(errors)
        name_key = " ".join(values["name"].split()).casefold()
        food = s.scalar(select(SavedFood).where(SavedFood.user_id == user.id, SavedFood.name_key == name_key))
        created = food is None
        if created:
            food = SavedFood(user_id=user.id, name_key=name_key)
            s.add(food)
        was_deleted = not created and food.deleted_at is not None
        food.name, food.serving_label = values["name"], values["serving_label"]
        food.kcal_per_serving, food.grams_per_serving = values["kcal_per_serving"], values["grams_per_serving"]
        food.deleted_at = None
        food.updated_at = db.utcnow()
        s.flush()
        grams = f" ({food.grams_per_serving:g} g)" if food.grams_per_serving else ""
        verb = "Saved" if created else ("Brought back and updated" if was_deleted else "Updated")
        return {"kind": "saved_food", "id": food.id, "created": created, "food": food_dict(food),
                "summary": f"{verb} {food.name}: {food.serving_label}{grams} is {food.kcal_per_serving:g} kcal.",
                "links": {"foods": links.page("foods")}}


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
        used = dict((fid, (n, last)) for fid, n, last in s.execute(
            select(FoodEntry.saved_food_id, func.count(), func.max(FoodEntry.date)).where(
                FoodEntry.user_id == user_id, FoodEntry.deleted_at.is_(None), FoodEntry.saved_food_id.is_not(None))
            .group_by(FoodEntry.saved_food_id)))
        foods = [f for f in s.scalars(select(SavedFood).where(SavedFood.user_id == user_id,
                                                              SavedFood.deleted_at.is_(None)))
                 if needle in f.name_key]
        foods.sort(key=lambda f: (-used.get(f.id, (0, None))[0], f.name_key))
        saved = [{**food_dict(f), "times_logged": used.get(f.id, (0, None))[0],
                  "last_logged": used[f.id][1].isoformat() if f.id in used else None} for f in foods[:count]]

        groups: dict[str, dict] = {}
        for e in s.scalars(select(FoodEntry).where(
                FoodEntry.user_id == user_id, FoodEntry.deleted_at.is_(None),
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
                     else "USDA did not answer: use a saved food or the person's own number.")
    summary = f"For '{rules.quote(text)}': {len(saved)} saved, {len(recent)} recent"
    summary += f", {len(found)} from USDA." if found is not None else ". " + usda_note
    return {"query": text, "saved": saved, "recent": recent, "usda": found, "usda_note": usda_note,
            "summary": summary}


def _limit(value) -> tuple[int | None, dict | None]:
    if value is None:
        return 8, None
    number = rules.to_float(value)
    if number is None or number != int(number) or not 1 <= number <= 25:
        return None, rules.error("invalid_field", f"limit '{rules.quote(value)}' is not a whole number from 1 to 25.",
                                 "Give limit from 1 to 25, or leave it out for 8.")
    return int(number), None
