"""The sixteen-week account: a year of weigh-ins, sixteen weeks of lifts, food, cardio and a waist, two goals.

The seed the data cases start from (evals/cases.json, {"service": "sixteen_weeks"}; contract data-on-request.md,
"The seed"). It lives here, outside app/, as four_weeks does: Spotter itself never adds it, and app/ never imports
from evals/.

sixteen_weeks(user_id)
    Writes the rows straight through the models, in one transaction: no call-log row, no write limit. Only into
    an empty account (services.account_empty) whose deleted saved foods hold none of SAVED_FOODS' names, else
    not_empty. Every row is logged_by "web"; settings are never touched. Every row carries the duplicate key its
    kind uses (lifts.session_key, body.weigh_in_key, measurements.measurement_key, food.entry_key per meal,
    cardio.cardio_key).
    Returns {added {lift_sessions, weigh_ins, measurements, saved_foods, food_entries, cardio_entries, goals},
    from, to} or {error, message, fix}. from is the oldest weigh-in's date.

    No randomness. n is days before today, in the person's zone; the sixteen weeks are n = 111 down to 0.
      weigh-ins     one a day for 364 days, n = 363 to 0. For n of 21 or more, 80 kg plus 0.02 kg for each day
                    past 21; below 21, 80 kg plus 0.05 kg for each day short of 21; in Decimal, kept at 1 dp,
                    halves up. Except three: n 330 is 91.2, 340 is 92.4, 350 is 90.6. Every other is at most 86.8
      lift sessions Monday, Wednesday and Friday of the sixteen weeks: 48, A and B in turn from A, oldest first.
                    A, its i-th time from 0: Back Squat 3x5 at min(70 + 2.5 i, 100) kg, Bench Press 3x5 at
                    40 + 2.5 i kg, Barbell Row 3x8 at 60 kg. B, its j-th time: Deadlift 3x5 at 100 + 2.5 j kg,
                    Overhead Press 3x5 at 30 + 1.25 j kg, Pull-Up 3x6 at 0 kg. Every session is 3 lower sets
                    and 6 upper
      cardio        n of 21 or more only: Tuesday Outdoor Run, 30 minutes, 5 km; Thursday Stationary Bike,
                    30 minutes. Calorie estimates use body weight now (metrics.bodyweight_now over the weigh-ins)
      measurements  waist on today's weekday, 16 weeks: 92 cm for the oldest, then 0.25 cm less each week
      saved foods   SAVED_FOODS
      food          n of 21 or more: OLD_MEALS, 1,680 kcal a day; below 21: NEW_MEALS, 1,980 kcal a day
      goals         Body weight 78 kg: start 82 kg on today less 112 days, target today plus 56, down.
                    Back Squat 150 kg: start 80 kg on today less 112 days, target today plus 84, up. Both open
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app import db, metrics, rules, users
from app.models import Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood, User
from app.services import account, body, cardio, food, lifts, measurements
from app.services.common import exercises, no_account

BY = "web"
DAYS = 112             # sixteen weeks: n = 111 down to 0
WEIGH_DAYS = 364       # n = 363 down to 0
RECENT = 21            # n below this is the recent stretch: the new menu, no cardio, a faster gain

WEIGHT_BASE = Decimal("80")
WEIGHT_OLD_STEP = Decimal("0.02")     # a day past 21, going back
WEIGHT_RECENT_STEP = Decimal("0.05")  # a day short of 21
HIGH_WEIGH_INS = {330: 91.2, 340: 92.4, 350: 90.6}

SESSION_DAYS = (0, 2, 4)  # Monday, Wednesday, Friday
PLAN_A = (("Back Squat", 3, 5), ("Bench Press", 3, 5), ("Barbell Row", 3, 8))
PLAN_B = (("Deadlift", 3, 5), ("Overhead Press", 3, 5), ("Pull-Up", 3, 6))

RUN_DAY, BIKE_DAY = 1, 3  # Tuesday, Thursday
CARDIO_MINUTES, RUN_KM = 30.0, 5.0

WAIST_FIRST, WAIST_STEP, WAIST_WEEKS = Decimal("92"), Decimal("0.25"), 16

SAVED_FOODS = (("Oats bowl", "1 bowl", 300), ("Roti", "1 roti", 120), ("Dal bowl", "1 bowl", 180),
               ("Rice bowl", "1 bowl", 200))

# Each meal: (meal, [(saved food name, servings) | (description, kcal, source)]).
BREAKFAST = ("breakfast", (("Oats bowl", 1), ("Tea with milk", 80, "you_said")))
LUNCH = ("lunch", (("Roti", 2), ("Dal bowl", 1), ("Mixed vegetable sabzi", 150, "claude_estimate")))
OLD_MEALS = (BREAKFAST, LUNCH,
             ("dinner", (("Rice bowl", 1), ("Dal bowl", 1), ("Chicken curry", 350, "claude_estimate"))))
NEW_MEALS = (BREAKFAST, LUNCH,
             ("dinner", (("Chicken biryani", 640, "you_said"), ("Dal bowl", 1))),
             ("snack", (("Coke 500 ml", 210, "you_said"),)))

GOALS = (  # kind, exercise, start kg, target kg, direction, target days after today
    ("bodyweight", None, 82.0, 78.0, "down", 56),
    ("lift", "Back Squat", 80.0, 150.0, "up", 84),
)

COUNTS = ("lift_sessions", "weigh_ins", "measurements", "saved_foods", "food_entries", "cardio_entries", "goals")


# ---------------------------------------------------------------- building the rows

def _kg(n: int) -> float:
    """The weigh-in n days before today."""
    if n in HIGH_WEIGH_INS:
        return HIGH_WEIGH_INS[n]
    if n >= RECENT:
        value = WEIGHT_BASE + WEIGHT_OLD_STEP * (n - RECENT)
    else:
        value = WEIGHT_BASE + WEIGHT_RECENT_STEP * (RECENT - n)
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _food_rows(user_id: int, on, meals: tuple, saved: dict) -> list[FoodEntry]:
    rows = []
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
                entries.append({"meal": meal, "description": description, "kcal": kcal, "source": source,
                                "estimated": source == "claude_estimate", "saved_food_id": None, "servings": None,
                                "usda_fdc_id": None, "grams": None})
        key = food.entry_key(on, [food.entry_tuple(e) for e in entries])  # one meal, as one log_food call
        rows += [FoodEntry(user_id=user_id, date=on, logged_by=BY, dedupe_key=key, **e) for e in entries]
    return rows


def _plan_kg(name: str, times: int) -> float:
    """A lift's weight the times-th time its session plan comes round, from 0."""
    return {"Back Squat": min(70 + 2.5 * times, 100), "Bench Press": 40 + 2.5 * times, "Barbell Row": 60,
            "Deadlift": 100 + 2.5 * times, "Overhead Press": 30 + 1.25 * times, "Pull-Up": 0}[name]


def _sessions(user_id: int, days: list, by_name: dict) -> list[LiftSession]:
    out = []
    for d in (d for d in days if d.weekday() in SESSION_DAYS):
        plan = PLAN_A if len(out) % 2 == 0 else PLAN_B
        times = len(out) // 2
        sets = []
        for name, count, reps in plan:
            sets += [{"exercise": name, "weight_kg": float(_plan_kg(name, times)), "reps": reps}] * count
        keyed = lifts.number_sets(sets, by_name)
        session = LiftSession(user_id=user_id, date=d, notes="", logged_by=BY,
                              dedupe_key=lifts.session_key(d, "", keyed))
        session.sets = [LiftSet(exercise_id=e, set_number=n, weight_kg=w, reps=r) for e, n, w, r, *_ in keyed]
        out.append(session)
    return out


def _cardio_rows(user_id: int, days: list, today, by_name: dict, kg: float) -> list[CardioEntry]:
    out = []
    for d in days:
        if (today - d).days < RECENT or d.weekday() not in (RUN_DAY, BIKE_DAY):
            continue
        values = {f: None for f in cardio.NUMBER_FIELDS}
        values["minutes"] = CARDIO_MINUTES
        if d.weekday() == RUN_DAY:
            activity = by_name["Outdoor Run"]
            values["distance_km"] = RUN_KM
        else:
            activity = by_name["Stationary Bike"]
        key = cardio.cardio_key(d, activity.id, values)  # the fields given, as log_cardio keys them
        speed = metrics.speed(values["distance_km"], values["minutes"])
        row = CardioEntry(user_id=user_id, date=d, exercise_id=activity.id, notes="", logged_by=BY, dedupe_key=key,
                          estimated_kcal=metrics.cardio_kcal(activity.met, kg, values["minutes"]),
                          estimate_weight_kg=kg, **{**values, "speed_kmh": speed})
        row.exercise = activity
        out.append(row)
    return out


def _measurement_rows(user_id: int, today) -> list[Measurement]:
    unit = rules.MEASUREMENTS["waist"][0]
    out = []
    for week in range(WAIST_WEEKS):  # oldest first
        d = today - timedelta(days=7 * (WAIST_WEEKS - 1 - week))
        value = float(WAIST_FIRST - WAIST_STEP * week)
        out.append(Measurement(user_id=user_id, date=d, kind="waist", value=value, unit=unit, logged_by=BY,
                               dedupe_key=measurements.measurement_key(d, "waist", value)))
    return out


def _goals(user_id: int, today, by_name: dict) -> list[Goal]:
    start = today - timedelta(days=DAYS)
    return [Goal(user_id=user_id, kind=kind, exercise_id=by_name[name].id if name else None, unit="kg",
                 start_value=first, start_date=start, direction=direction, target_value=target,
                 target_date=today + timedelta(days=after), status="open", text="")
            for kind, name, first, target, direction, after in GOALS]


# ---------------------------------------------------------------- the seed

def sixteen_weeks(user_id: int) -> dict:
    """Returns {added {the seven counts}, from, to} or {error, message, fix}. See the module notes."""
    state = account.account_empty(user_id)
    if "error" in state:
        return state
    if not state["empty"]:
        return rules.error("not_empty", "The sixteen weeks go only into an empty account.",
                           "Seed a new person: this one already has entries, saved foods or open goals.")
    with db.session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        days = [today - timedelta(days=n) for n in range(DAYS - 1, -1, -1)]  # oldest first
        names = {name.casefold() for name, _, _ in SAVED_FOODS}
        if s.scalar(select(SavedFood.id).where(SavedFood.user_id == user.id, SavedFood.name_key.in_(names)).limit(1)):
            return rules.error("not_empty", "A deleted saved food of this account holds one of the sixteen weeks' "
                               "names.", "Seed a new person.")
        by_name = {e.name: e for e in exercises(s)}

        weighed = []
        for n in range(WEIGH_DAYS - 1, -1, -1):
            d, kg = today - timedelta(days=n), _kg(n)
            weighed.append(Bodyweight(user_id=user.id, date=d, kg=kg, logged_by=BY, dedupe_key=body.weigh_in_key(d, kg)))
        saved = {name: SavedFood(user_id=user.id, name=name, name_key=name.casefold(), serving_label=label,
                                 kcal_per_serving=float(kcal))
                 for name, label, kcal in SAVED_FOODS}
        s.add_all([*weighed, *saved.values()])
        s.flush()  # the saved foods' ids, for the entries that name them
        eaten = [row for d in days
                 for row in _food_rows(user.id, d, OLD_MEALS if (today - d).days >= RECENT else NEW_MEALS, saved)]
        sessions = _sessions(user.id, days, by_name)
        kg = metrics.bodyweight_now([(b.date, b.kg) for b in weighed], today)
        moved = _cardio_rows(user.id, days, today, by_name, kg)
        measured = _measurement_rows(user.id, today)
        goals = _goals(user.id, today, by_name)
        s.add_all([*eaten, *sessions, *moved, *measured, *goals])
        s.flush()
        added = {"lift_sessions": len(sessions), "weigh_ins": len(weighed), "measurements": len(measured),
                 "saved_foods": len(saved), "food_entries": len(eaten), "cardio_entries": len(moved),
                 "goals": len(goals)}
        return {"added": {k: added[k] for k in COUNTS}, "from": weighed[0].date.isoformat(), "to": today.isoformat()}
