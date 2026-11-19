"""The shared exercise list. The same name every time, so charts and progress line up.

Each row has a key (key_of: the name by the rule of contract exercise-list.md), which never changes once a row is
on the list: the database knows a row by it, not by its name. sync(session) matches the table's rows to the list by
key: it adds a listed row the table lacks, brings a held row's name, category, MET and Compendium code up to the
list, retires a row whose key is not listed and brings back one that is. Rows are never deleted, and running it
twice changes nothing. The deploy runs it after the migrations:

    python -m app.exercises
"""

import re
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Exercise

CATEGORIES = ["squat", "hinge", "push", "pull", "carry", "core", "accessory"]

# (name, category)
LIFTS: list[tuple[str, str]] = [
    ("Back Squat", "squat"),
    ("Front Squat", "squat"),
    ("Goblet Squat", "squat"),
    ("Leg Press", "squat"),
    ("Bulgarian Split Squat", "squat"),
    ("Walking Lunge", "squat"),
    ("Bodyweight Squat", "squat"),
    ("Step-Up", "squat"),
    ("Deadlift", "hinge"),
    ("Romanian Deadlift", "hinge"),
    ("Dumbbell Romanian Deadlift", "hinge"),
    ("Hip Thrust", "hinge"),
    ("Glute Bridge", "hinge"),
    ("Kettlebell Swing", "hinge"),
    ("Bench Press", "push"),
    ("Incline Dumbbell Press", "push"),
    ("Dumbbell Bench Press", "push"),
    ("Overhead Press", "push"),
    ("Dumbbell Shoulder Press", "push"),
    ("Push-Up", "push"),
    ("Dip", "push"),
    ("Pull-Up", "pull"),
    ("Lat Pulldown", "pull"),
    ("Barbell Row", "pull"),
    ("Dumbbell Row", "pull"),
    ("Seated Cable Row", "pull"),
    ("Inverted Row", "pull"),
    ("Face Pull", "pull"),
    ("Farmer's Carry", "carry"),
    ("Suitcase Carry", "carry"),
    ("Plank", "core"),
    ("Side Plank", "core"),
    ("Dead Bug", "core"),
    ("Hanging Knee Raise", "core"),
    ("Pallof Press", "core"),
    ("Leg Curl", "accessory"),
    ("Leg Extension", "accessory"),
    ("Calf Raise", "accessory"),
    ("Dumbbell Curl", "accessory"),
    ("Triceps Pushdown", "accessory"),
    ("Lateral Raise", "accessory"),
]

# The muscles each lift works and its body region (contract data-on-request.md, decision 17), from free-exercise-db
# (yuhonas/free-exercise-db on GitHub, The Unlicense, read on 2026-11-16): each primary muscle 1 a set, each
# secondary 0.5 (R13). Code only, never a column: sync and list_exercises do not read them. The region is worked from
# the lift's primary muscle (REGION_OF), save the two carries, which are full_body. Suitcase Carry has no entry there.
MUSCLE_NAMES = ("abdominals", "abductors", "adductors", "biceps", "calves", "chest", "forearms", "glutes", "hamstrings",
                "lats", "lower back", "middle back", "neck", "quadriceps", "shoulders", "traps", "triceps")
REGION_NAMES = ("upper", "lower", "core", "full_body")
REGION_OF = {**dict.fromkeys(("quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors", "lower back"),
                             "lower"),
             **dict.fromkeys(("chest", "shoulders", "lats", "middle back", "traps", "biceps", "triceps", "forearms",
                              "neck"), "upper"),
             "abdominals": "core"}
FULL_BODY = ("Farmer's Carry", "Suitcase Carry")  # a carry is grip, legs and trunk at once

# lift: (its free-exercise-db entry, its primary muscles, its secondary muscles)
_SOURCE: dict[str, tuple[str | None, tuple[str, ...], tuple[str, ...]]] = {
    "Back Squat": ("Barbell_Full_Squat", ("quadriceps",), ("calves", "glutes", "hamstrings", "lower back")),
    "Front Squat": ("Front_Barbell_Squat", ("quadriceps",), ("calves", "glutes", "hamstrings")),
    "Goblet Squat": ("Goblet_Squat", ("quadriceps",), ("calves", "glutes", "hamstrings", "shoulders")),
    "Leg Press": ("Leg_Press", ("quadriceps",), ("calves", "glutes", "hamstrings")),
    "Bulgarian Split Squat": ("Split_Squat_with_Dumbbells", ("quadriceps",), ("glutes", "hamstrings")),
    "Walking Lunge": ("Bodyweight_Walking_Lunge", ("quadriceps",), ("calves", "glutes", "hamstrings")),
    "Bodyweight Squat": ("Bodyweight_Squat", ("quadriceps",), ("glutes", "hamstrings")),
    "Step-Up": ("Dumbbell_Step_Ups", ("quadriceps",), ("calves", "glutes", "hamstrings")),
    "Deadlift": ("Barbell_Deadlift", ("lower back",),
                 ("calves", "forearms", "glutes", "hamstrings", "lats", "middle back", "quadriceps", "traps")),
    "Romanian Deadlift": ("Romanian_Deadlift", ("hamstrings",), ("calves", "glutes", "lower back")),
    "Dumbbell Romanian Deadlift": ("Stiff-Legged_Dumbbell_Deadlift", ("hamstrings",), ("glutes", "lower back")),
    "Hip Thrust": ("Barbell_Hip_Thrust", ("glutes",), ("calves", "hamstrings")),
    "Glute Bridge": ("Barbell_Glute_Bridge", ("glutes",), ("calves", "hamstrings")),
    "Kettlebell Swing": ("One-Arm_Kettlebell_Swings", ("hamstrings",), ("calves", "glutes", "lower back", "shoulders")),
    "Bench Press": ("Barbell_Bench_Press_-_Medium_Grip", ("chest",), ("shoulders", "triceps")),
    "Incline Dumbbell Press": ("Incline_Dumbbell_Press", ("chest",), ("shoulders", "triceps")),
    "Dumbbell Bench Press": ("Dumbbell_Bench_Press", ("chest",), ("shoulders", "triceps")),
    "Overhead Press": ("Standing_Military_Press", ("shoulders",), ("triceps",)),
    "Dumbbell Shoulder Press": ("Dumbbell_Shoulder_Press", ("shoulders",), ("triceps",)),
    "Push-Up": ("Pushups", ("chest",), ("shoulders", "triceps")),
    "Dip": ("Parallel_Bar_Dip", ("triceps",), ("chest", "shoulders")),
    "Pull-Up": ("Pullups", ("lats",), ("biceps", "middle back")),
    "Lat Pulldown": ("Wide-Grip_Lat_Pulldown", ("lats",), ("biceps", "middle back", "shoulders")),
    "Barbell Row": ("Bent_Over_Barbell_Row", ("middle back",), ("biceps", "lats", "shoulders")),
    "Dumbbell Row": ("One-Arm_Dumbbell_Row", ("middle back",), ("biceps", "lats", "shoulders")),
    "Seated Cable Row": ("Seated_Cable_Rows", ("middle back",), ("biceps", "lats", "shoulders")),
    "Inverted Row": ("Inverted_Row", ("middle back",), ("lats",)),
    "Face Pull": ("Face_Pull", ("shoulders",), ("middle back",)),
    "Farmer's Carry": ("Farmers_Walk", ("forearms",),
                       ("abdominals", "glutes", "hamstrings", "lower back", "quadriceps", "traps")),
    "Suitcase Carry": (None, (), ()),
    "Plank": ("Plank", ("abdominals",), ()),
    "Side Plank": ("Side_Bridge", ("abdominals",), ("shoulders",)),
    "Dead Bug": ("Dead_Bug", ("abdominals",), ()),
    "Hanging Knee Raise": ("Hanging_Leg_Raise", ("abdominals",), ()),
    "Pallof Press": ("Pallof_Press", ("abdominals",), ("chest", "shoulders", "triceps")),
    "Leg Curl": ("Lying_Leg_Curls", ("hamstrings",), ()),
    "Leg Extension": ("Leg_Extensions", ("quadriceps",), ()),
    "Calf Raise": ("Standing_Calf_Raises", ("calves",), ()),
    "Dumbbell Curl": ("Dumbbell_Bicep_Curl", ("biceps",), ("forearms",)),
    "Triceps Pushdown": ("Triceps_Pushdown", ("triceps",), ()),
    "Lateral Raise": ("Side_Lateral_Raise", ("shoulders",), ()),
}
FREE_EXERCISE_DB = {name: entry for name, (entry, _, _) in _SOURCE.items()}
MUSCLES: dict[str, dict[str, float]] = {
    name: {**dict.fromkeys(primary, 1.0), **dict.fromkeys(secondary, 0.5)}
    for name, (_, primary, secondary) in _SOURCE.items()}
REGIONS: dict[str, str] = {
    name: "full_body" if name in FULL_BODY else REGION_OF[primary[0]] for name, (_, primary, _) in _SOURCE.items()}

CARDIO_CATEGORIES = ["walk", "run", "cycle", "row", "swim", "machine", "other"]

# Cardio: (name, category, MET, Compendium code), kind "cardio". Every MET and code is a row of the
# 2024 Adult Compendium of Physical Activities (Herrmann SD et al., pacompendium.com, CC BY-NC-ND 4.0),
# at a moderate, general intensity. The comment quotes the row's description.
CARDIO: list[tuple[str, str, float, str]] = [
    ("Treadmill Walk", "walk", 3.8, "17355"),      # Walking, treadmill, 3.0 to 3.4 mph (4.8 to 5.5 km/h), 0% grade
    # The 2024 Compendium has no motorized-treadmill running row (only curved, non-motorized ones),
    # so a treadmill run uses the same running row as an outdoor run.
    ("Treadmill Run", "run", 9.3, "12050"),        # Running, 6-6.3 mph (10 min/mile)
    ("Outdoor Walk", "walk", 3.8, "17190"),        # Walking, 2.8 to 3.4 mph, level, moderate pace, firm surface
    ("Outdoor Run", "run", 9.3, "12050"),          # Running, 6-6.3 mph (10 min/mile)
    ("Stationary Bike", "cycle", 6.8, "01200"),    # Bicycling, stationary, general
    ("Outdoor Cycling", "cycle", 7.0, "01014"),    # Bicycling, general
    ("Rowing Machine", "row", 5.0, "02071"),       # Rowing, stationary ergometer, general, <100 watts, moderate effort
    ("Elliptical", "machine", 5.0, "02048"),       # The elliptical machine row, moderate effort
    ("Stair Climber", "machine", 9.3, "02065"),    # Stair treadmill ergometer, general (the only stair-machine row)
    ("Jump Rope", "other", 11.8, "15551"),         # Rope jumping, moderate pace, general, 100 to 120 skips/min
    ("Swimming", "swim", 6.0, "18310"),            # Swimming, leisurely, not lap swimming, general
    ("Hiking", "walk", 6.0, "17080"),              # Hiking, cross country
    ("Circuit Training", "other", 5.0, "02035"),   # Circuit training, moderate effort
    ("Badminton", "other", 5.5, "15030"),          # Badminton, social singles and doubles, general
    ("Football", "other", 7.0, "15610"),           # Soccer, casual, general
]
COMPENDIUM = "2024 Adult Compendium of Physical Activities"


def key_of(name: str) -> str:
    """A row's key from its name: lowercased, apostrophes dropped, "&" as "and", and each run of hyphens, slashes,
    brackets, commas, full stops and spaces as one underscore, none at either end. "Farmer's Carry" is farmers_carry.
    migrations/versions/0013_exercise_list.py holds the same rule."""
    key = name.lower().replace("'", "").replace(chr(0x2019), "").replace("&", "and")
    return re.sub(r"[-/(),.\s]+", "_", key).strip("_")


def rows() -> list[dict]:
    """Every exercise on the list, as column values."""
    lifts = [{"key": key_of(name), "name": name, "kind": "lift", "category": category, "met": None,
              "compendium_code": None} for name, category in LIFTS]
    cardio = [{"key": key_of(name), "name": name, "kind": "cardio", "category": category, "met": met,
               "compendium_code": code} for name, category, met, code in CARDIO]
    return lifts + cardio


SYNCED = ("name", "category", "met", "compendium_code")  # what sync brings up to the list on a held row


class SyncStopped(Exception):
    """A listed row would take a name another row holds. Its text is one line, the two keys."""


def sync(session: Session) -> int:
    """Match the table to the list by key. Returns how many rows were added.

    A listed key the table lacks is added. A held row takes the list's name, category, met and compendium_code when
    they differ, so a rename or a corrected Compendium value reaches a live database; its id and kind stay. A row
    whose key is not listed is retired, and one listed again is brought back. No row is deleted. A listed row whose
    name another row holds stops the sync with SyncStopped, before any change. Running it twice changes nothing.
    The caller commits."""
    held = {e.key: e for e in session.scalars(select(Exercise))}
    key_holding = {e.name: e.key for e in held.values()}
    listed = rows()
    for row in listed:
        other = key_holding.get(row["name"])
        if other is not None and other != row["key"]:
            raise SyncStopped(f"Exercise sync stopped: {row['key']} and {other} would hold one name. "
                              "Nothing was changed.")
    added = []
    for row in listed:
        e = held.get(row["key"])
        if e is None:
            added.append(Exercise(**row))
            continue
        for column in SYNCED:
            if getattr(e, column) != row[column]:
                setattr(e, column, row[column])
        if e.retired:
            e.retired = False
    keys = {row["key"] for row in listed}
    for key, e in held.items():
        if key not in keys and not e.retired:
            e.retired = True
    session.add_all(added)
    session.flush()
    return len(added)


def main() -> None:
    from app.db import DatabaseNotConfigured, dispose, session_scope

    try:
        with session_scope() as session:
            added = sync(session)
            total = len(session.scalars(select(Exercise.id)).all())
    except DatabaseNotConfigured as problem:
        sys.exit(f"FAILED: {problem}")  # one line on stderr, exit status 1
    except SyncStopped as problem:
        sys.exit(str(problem))  # one line on stderr, exit status 1: session_scope rolled back
    finally:
        dispose()
    print(f"Exercise list synced: {added} added, {total} in all.")


if __name__ == "__main__":
    main()
