"""Sample data: four weeks of everything, in one click, on an empty account; removed in one click.

sample_state(user_id) -> {empty, sample_rows}
    A read. empty: no live row in the five entry tables, no live saved food and no open goal.
    Soft-deleted rows and closed goals (hit or dropped) do not count: a goal has no delete, so a
    dropped one must not block sample data for good. sample_rows: every sample row, live or deleted,
    which is what removal deletes. The pages decide what to offer from it.
    A deleted saved food keeps its name (names are unique per person, deleted ones included), so a
    sample food whose name it holds is added as "<name> (sample)".

add_sample_data(user_id, by)
    A write. Only into an empty account (else not_empty), and never twice (already_sample, checked
    first). One transaction, after lock_user and check_write_limit. Every row is_sample true and
    logged_by "web"; settings are never touched. Every row carries the duplicate key its kind uses
    (lifts.session_key, body.weigh_in_key, measurements.measurement_key, food.entry_key per meal,
    cardio.cardio_key), so a later real entry is judged the same way.

    Deterministic: the 28 days ending today, in the person's zone, and random.Random(SEED). The
    generator draws in one fixed order: the 28 weigh-ins' noise, oldest first; then for each day,
    oldest first, the day's total, whether it has a snack, and the snack's kcal when it has one; then
    the Stationary Bike machine numbers, oldest first. Change that order and every number changes.
      weigh-ins     one a day: 80.0 kg - 0.3 kg a week, plus uniform noise of +-0.3 kg, 1 dp
      lift sessions Monday, Wednesday and Friday (any 28 days hold 4 of each weekday: 12 sessions),
                    A and B in turn from A. A: Back Squat 3x5, Bench Press 3x5, Barbell Row 3x8.
                    B: Deadlift 1x5, Overhead Press 3x5, Pull-Up 3x6 at 0 kg. Each lift starts at
                    START_KG and adds STEP_KG each session it is in.
      cardio        Tuesday Treadmill Walk 30 min; Thursday Stationary Bike 25 min with a machine number;
                    Saturday Outdoor Run 5 km, 31.0 minutes then 0.5 less each week. Calorie estimates
                    use body weight now (metrics.bodyweight_now over the sample weigh-ins), as any
                    back-dated entry's do.
      saved foods   SAVED_FOODS
      food          every day: breakfast, lunch and dinner from saved foods and stated numbers, two
                    marked estimates sized so the day lands on its drawn total (2,100 to 2,300 kcal),
                    and a stated snack on some days. Tags saved_food, you_said and claude_estimate all show.
      measurements  waist and resting_hr weekly on today's weekday, 4 each (WAIST, RESTING_HR)
      goals         Body weight 77 kg by today + 56; Back Squat 120 kg by today + 84; Outdoor Run 5 km in
                    27:00 by today + 56. Each starts where its own series starts: the first value and its
                    date, what set_goal would have stored that day.

remove_sample_data(user_id, by)
    A write. Hard-deletes every sample row, live or deleted (a session's sets go with it). Nothing to
    remove is not an error. A real entry the person logged against a sample food keeps its numbers
    and its tag but loses the link (ON DELETE SET NULL); the summary counts those.
"""

import random
from datetime import timedelta

from sqlalchemy import delete, func, select

from app import db, formatting, links, metrics, rules, users
from app.models import (
    Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood, User, entry_models,
)
from app.services import body, cardio, food, lifts, measurements
from app.services.common import check_by, check_write_limit, exercises, lock_user, no_account

SEED = 20261110
DAYS = 28
BY = "web"

WEIGHT_START, WEIGHT_PER_WEEK, WEIGHT_NOISE = 80.0, -0.3, 0.3
SESSION_DAYS = (0, 2, 4)  # Monday, Wednesday, Friday
PLAN_A = (("Back Squat", 3, 5), ("Bench Press", 3, 5), ("Barbell Row", 3, 8))
PLAN_B = (("Deadlift", 1, 5), ("Overhead Press", 3, 5), ("Pull-Up", 3, 6))
START_KG = {"Back Squat": 80, "Bench Press": 60, "Barbell Row": 50, "Deadlift": 100, "Overhead Press": 40,
            "Pull-Up": 0}
STEP_KG = {"Back Squat": 2.5, "Bench Press": 2.5, "Barbell Row": 2.5, "Deadlift": 5, "Overhead Press": 2.5,
           "Pull-Up": 0}
WALK_DAY, BIKE_DAY, RUN_DAY = 1, 3, 5  # Tuesday, Thursday, Saturday
WALK_MINUTES, BIKE_MINUTES, RUN_KM, RUN_FIRST_MINUTES, RUN_STEP = 30, 25, 5, 31.0, 0.5
BIKE_MACHINE_KCAL = (150, 220)
SAVED_FOODS = (("Roti", "1 roti", 120), ("Dal bowl", "1 bowl", 180), ("Rice bowl", "1 bowl", 200),
               ("Oats bowl", "1 bowl", 300))
DAY_KCAL = (2100, 2300)
SNACK_KCAL = (150, 250)
SNACK_CHANCE = 0.5
SABZI_SHARE = 0.4  # of the kcal the two estimates make up; the curry takes the rest
WAIST = (88.0, 87.5, 87.0, 86.5)
RESTING_HR = (64, 63, 62, 62)
GOAL_DAYS = {"bodyweight": 56, "lift": 84, "cardio_best": 56}
TARGETS = {"bodyweight": 77, "lift": 120, "cardio_best": 27.0}

# Each meal: (meal, [(saved food name, servings) | (description, kcal, source)]). "sabzi" and "curry"
# are worked out per day; every other number is fixed.
MEALS = (
    ("breakfast", (("Oats bowl", 1), ("Tea with milk", 80, "you_said"))),
    ("lunch", (("Roti", 2), ("Dal bowl", 1), ("Mixed vegetable sabzi", "sabzi", "claude_estimate"))),
    ("dinner", (("Rice bowl", 1), ("Dal bowl", 1), ("Chicken curry", "curry", "claude_estimate"))),
)
SNACK = "Banana and peanuts"
FIXED_KCAL = 300 + 80 + 120 * 2 + 180 + 200 + 180  # everything but the two estimates and the snack

COUNTS = ("lift_sessions", "weigh_ins", "measurements", "saved_foods", "food_entries", "cardio_entries", "goals")
MODELS = {"food_entries": FoodEntry, "cardio_entries": CardioEntry, "lift_sessions": LiftSession,
          "weigh_ins": Bodyweight, "measurements": Measurement, "goals": Goal, "saved_foods": SavedFood}


# ---------------------------------------------------------------- the state

def _sample_rows(s, user_id: int) -> int:
    return sum(s.scalar(select(func.count()).select_from(m).where(m.user_id == user_id, m.is_sample.is_(True)))
               for m in MODELS.values())


def _is_empty(s, user_id: int) -> bool:
    for model in (*entry_models(), SavedFood):
        if s.scalar(select(model.id).where(model.user_id == user_id, model.deleted_at.is_(None)).limit(1)):
            return False
    return not s.scalar(select(Goal.id).where(Goal.user_id == user_id, Goal.status == "open").limit(1))


def _free_name(name: str, taken: set[str]) -> str:
    """A sample food's name, or "<name> (sample)" when a deleted saved food of the person's holds it:
    names are unique per person, deleted ones included, so a deleted one can come back as it was."""
    return name if name.casefold() not in taken else f"{name} (sample)"


def sample_state(user_id: int) -> dict:
    """Returns {empty, sample_rows} or {error, message, fix}. See the module notes."""
    with db.new_session() as s:
        if s.get(User, user_id) is None:
            return no_account()
        return {"empty": _is_empty(s, user_id), "sample_rows": _sample_rows(s, user_id)}


# ---------------------------------------------------------------- building the rows

def _weights(rng: random.Random, days: list) -> list[float]:
    return [round(WEIGHT_START + WEIGHT_PER_WEEK * i / 7 + rng.uniform(-WEIGHT_NOISE, WEIGHT_NOISE), 1)
            for i in range(len(days))]


def _menus(rng: random.Random, days: list) -> list[dict]:
    """Per day: its drawn total, the snack's kcal (0 for none) and the two estimates."""
    out = []
    for _ in days:
        total = rng.randint(*DAY_KCAL)
        snack = rng.randint(*SNACK_KCAL) if rng.random() < SNACK_CHANCE else 0
        rest = total - FIXED_KCAL - snack
        sabzi = round(rest * SABZI_SHARE)
        out.append({"total": total, "snack": snack, "sabzi": sabzi, "curry": rest - sabzi})
    return out


def _food_rows(user_id: int, on, menu: dict, saved: dict) -> list[FoodEntry]:
    rows = []
    meals = list(MEALS) + ([("snack", ((SNACK, menu["snack"], "you_said"),))] if menu["snack"] else [])
    for meal, items in meals:
        entries = []
        for item in items:
            if len(item) == 2:
                name, servings = item
                f = saved[name]
                entries.append({"meal": meal, "description": f.name, "kcal": int(round(f.kcal_per_serving * servings)),
                                "source": "saved_food", "estimated": False, "saved_food_id": f.id,
                                "servings": float(servings), "usda_fdc_id": None, "grams": None})
            else:
                description, kcal, source = item
                kcal = menu[kcal] if isinstance(kcal, str) else kcal
                entries.append({"meal": meal, "description": description, "kcal": kcal, "source": source,
                                "estimated": source == "claude_estimate", "saved_food_id": None, "servings": None,
                                "usda_fdc_id": None, "grams": None})
        key = food.entry_key(on, [food.entry_tuple(e) for e in entries])  # one meal, as one log_food call
        rows += [FoodEntry(user_id=user_id, date=on, logged_by=BY, dedupe_key=key, is_sample=True, **e)
                 for e in entries]
    return rows


def _sessions(user_id: int, days: list, by_name: dict) -> list[LiftSession]:
    done: dict[str, int] = {}
    out = []
    for d in (d for d in days if d.weekday() in SESSION_DAYS):
        plan = PLAN_A if len(out) % 2 == 0 else PLAN_B
        sets = []
        for name, count, reps in plan:
            weight = START_KG[name] + STEP_KG[name] * done.get(name, 0)
            done[name] = done.get(name, 0) + 1
            sets += [{"exercise": name, "weight_kg": float(weight), "reps": reps}] * count
        keyed = lifts.number_sets(sets, by_name)
        session = LiftSession(user_id=user_id, date=d, notes="", logged_by=BY, is_sample=True,
                              dedupe_key=lifts.session_key(d, "", keyed))
        session.sets = [LiftSet(exercise_id=e, set_number=n, weight_kg=w, reps=r) for e, n, w, r in keyed]
        out.append(session)
    return out


def _cardio_rows(rng: random.Random, user_id: int, days: list, by_name: dict, kg: float) -> list[CardioEntry]:
    out = []
    runs = 0
    for d in days:
        values = {f: None for f in cardio.NUMBER_FIELDS}
        if d.weekday() == WALK_DAY:
            activity = by_name["Treadmill Walk"]
            values["minutes"] = float(WALK_MINUTES)
        elif d.weekday() == BIKE_DAY:
            activity = by_name["Stationary Bike"]
            values.update(minutes=float(BIKE_MINUTES), machine_kcal=rng.randint(*BIKE_MACHINE_KCAL))
        elif d.weekday() == RUN_DAY:
            activity = by_name["Outdoor Run"]
            values.update(minutes=RUN_FIRST_MINUTES - RUN_STEP * runs, distance_km=float(RUN_KM))
            runs += 1
        else:
            continue
        key = cardio.cardio_key(d, activity.id, values)  # the fields given, as log_cardio keys them
        speed = metrics.speed(values["distance_km"], values["minutes"])
        row = CardioEntry(user_id=user_id, date=d, exercise_id=activity.id, notes="", logged_by=BY, is_sample=True,
                          dedupe_key=key, estimated_kcal=metrics.cardio_kcal(activity.met, kg, values["minutes"]),
                          estimate_weight_kg=kg, **{**values, "speed_kmh": speed})
        row.exercise = activity
        out.append(row)
    return out


def _measurement_rows(user_id: int, today) -> list[Measurement]:
    out = []
    for kind, values in (("waist", WAIST), ("resting_hr", RESTING_HR)):
        unit = rules.MEASUREMENTS[kind][0]
        for n, value in enumerate(values):
            d = today - timedelta(days=7 * (len(values) - 1 - n))
            out.append(Measurement(user_id=user_id, date=d, kind=kind, value=value, unit=unit, logged_by=BY,
                                   is_sample=True, dedupe_key=measurements.measurement_key(d, kind, value)))
    return out


def _goals(user_id: int, today, weighed: list, squat_first, run_first, by_name: dict) -> list[Goal]:
    """The three goals, each starting at the first value of its own series, on that value's date."""
    first_weight = weighed[0]
    return [
        Goal(user_id=user_id, kind="bodyweight", unit="kg", start_value=first_weight.kg, start_date=first_weight.date,
             direction="down", target_value=TARGETS["bodyweight"],
             target_date=today + timedelta(days=GOAL_DAYS["bodyweight"]), status="open", text="", is_sample=True),
        Goal(user_id=user_id, kind="lift", exercise_id=by_name["Back Squat"].id, unit="kg",
             start_value=squat_first[1], start_date=squat_first[0], direction="up", target_value=TARGETS["lift"],
             target_date=today + timedelta(days=GOAL_DAYS["lift"]), status="open", text="", is_sample=True),
        Goal(user_id=user_id, kind="cardio_best", exercise_id=by_name["Outdoor Run"].id, distance_km=float(RUN_KM),
             unit="min", start_value=run_first.minutes, start_date=run_first.date, direction="down",
             target_value=TARGETS["cardio_best"], target_date=today + timedelta(days=GOAL_DAYS["cardio_best"]),
             status="open", text="", is_sample=True),
    ]


# ---------------------------------------------------------------- add and remove

def add_sample_data(user_id: int, by: str) -> dict:
    """Returns {added {lift_sessions, weigh_ins, measurements, saved_foods, food_entries, cardio_entries,
    goals}, from, to, summary, links {today, progress, goals}} or {error, message, fix}."""
    err = check_by(by)
    if err:
        return err
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        if _sample_rows(s, user.id):
            return rules.error("already_sample", "This account already holds sample data.",
                               "Nothing to add: it is there already. Remove sample data on Settings clears it.")
        if not _is_empty(s, user.id):
            return rules.error("not_empty", "Sample data goes only into an empty account.",
                               "Delete the entries on the day pages and the saved foods on the Foods page, and mark "
                               "open goals dropped on the Goals page, first. Or keep logging for real.")
        today = users.today_for(user)
        days = [today - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
        rng = random.Random(SEED)
        by_name = {e.name: e for e in exercises(s)}

        weighed = [Bodyweight(user_id=user.id, date=d, kg=kg, logged_by=BY, is_sample=True,
                              dedupe_key=body.weigh_in_key(d, kg)) for d, kg in zip(days, _weights(rng, days))]
        menus = _menus(rng, days)
        taken = set(s.scalars(select(SavedFood.name_key).where(SavedFood.user_id == user.id)))
        saved = {name: SavedFood(user_id=user.id, name=_free_name(name, taken), name_key=_free_name(name, taken)
                                 .casefold(), serving_label=label, kcal_per_serving=float(kcal), is_sample=True)
                 for name, label, kcal in SAVED_FOODS}
        s.add_all([*weighed, *saved.values()])
        s.flush()  # the saved foods' ids, for the entries that name them
        eaten = [row for d, menu in zip(days, menus) for row in _food_rows(user.id, d, menu, saved)]
        sessions = _sessions(user.id, days, by_name)
        kg = metrics.bodyweight_now([(b.date, b.kg) for b in weighed], today)
        moved = _cardio_rows(rng, user.id, days, by_name, kg)
        measured = _measurement_rows(user.id, today)
        squat = sessions[0]  # the first session is an A session, which opens with the squat
        squat_first = (squat.date, metrics.session_best([(x.weight_kg, x.reps) for x in squat.sets
                                                         if x.exercise_id == by_name["Back Squat"].id]))
        run_first = next(c for c in moved if c.exercise_id == by_name["Outdoor Run"].id)
        goals = _goals(user.id, today, weighed, squat_first, run_first, by_name)
        s.add_all([*eaten, *sessions, *moved, *measured, *goals])
        s.flush()
        added = {"lift_sessions": len(sessions), "weigh_ins": len(weighed), "measurements": len(measured),
                 "saved_foods": len(saved), "food_entries": len(eaten), "cardio_entries": len(moved),
                 "goals": len(goals)}
        return {"added": added, "from": days[0].isoformat(), "to": today.isoformat(),
                "summary": (f"Added sample data for {formatting.days(days[0], today, today)}: {_listed(added)}. "
                            "Every row is marked sample. Remove sample data on Settings clears it all."),
                "links": {"today": links.page("today"), "progress": links.page("progress"),
                          "goals": links.page("goals")}}


def _listed(counts: dict) -> str:
    words = {"lift_sessions": "lift sessions", "weigh_ins": "weigh-ins", "measurements": "measurements",
             "saved_foods": "saved foods", "food_entries": "food entries", "cardio_entries": "cardio entries",
             "goals": "goals"}
    return ", ".join(f"{formatting.show(counts[k], 'count')} {words[k]}" for k in COUNTS)


def remove_sample_data(user_id: int, by: str) -> dict:
    """Returns {removed {the same counts as added}, summary, links {today}} or {error, message, fix}."""
    err = check_by(by)
    if err:
        return err
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        # The person's own entries logged against a sample food keep their numbers and tag; the schema
        # clears their link (ON DELETE SET NULL). Counted first, so the summary can say so.
        sample_foods = select(SavedFood.id).where(SavedFood.user_id == user.id, SavedFood.is_sample.is_(True))
        unlinked = s.scalar(select(func.count()).select_from(FoodEntry).where(
            FoodEntry.user_id == user.id, FoodEntry.is_sample.is_(False), FoodEntry.saved_food_id.in_(sample_foods)))
        removed = {}
        # Entries before the saved foods they may name; a session's sets go with it (ON DELETE CASCADE).
        for name in ("food_entries", "cardio_entries", "lift_sessions", "weigh_ins", "measurements", "goals",
                     "saved_foods"):
            model = MODELS[name]
            removed[name] = s.execute(delete(model).where(model.user_id == user.id, model.is_sample.is_(True))
                                      .execution_options(synchronize_session=False)).rowcount
        removed = {k: removed[k] for k in COUNTS}
        total = sum(removed.values())
        summary = (f"Removed the sample data: {_listed(removed)}." if total
                   else "There was no sample data to remove.")
        if unlinked:
            one = unlinked == 1
            summary += (f" {formatting.show(unlinked, 'count')} real {'entry was' if one else 'entries were'} logged "
                        f"against a sample food. {'It keeps its' if one else 'They keep their'} numbers and "
                        f"{'loses' if one else 'lose'} the link.")
        return {"removed": removed, "summary": summary, "links": {"today": links.page("today")}}
