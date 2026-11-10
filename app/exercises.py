"""The shared exercise list. The same name every time, so charts and progress line up.

sync(session) adds any exercise missing from the database, matched by name, and brings an existing
cardio row's MET and Compendium code up to the list; nothing else changes, and running it twice
changes nothing. The deploy runs it after the migrations:

    python -m app.exercises
"""

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


def rows() -> list[dict]:
    """Every exercise on the list, as column values."""
    lifts = [{"name": name, "kind": "lift", "category": category, "met": None, "compendium_code": None}
             for name, category in LIFTS]
    cardio = [{"name": name, "kind": "cardio", "category": category, "met": met, "compendium_code": code}
              for name, category, met, code in CARDIO]
    return lifts + cardio


def sync(session: Session) -> int:
    """Add every listed exercise the database does not have yet, by name. Returns how many were added.

    An existing row keeps its name, kind and category. An existing cardio row takes the list's met and
    compendium_code when they differ, so a corrected Compendium value reaches a live database. Running
    it twice changes nothing. The caller commits."""
    existing = {e.name: e for e in session.scalars(select(Exercise))}
    listed = rows()
    added = [Exercise(**row) for row in listed if row["name"] not in existing]
    for row in listed:
        held = existing.get(row["name"])
        if held is not None and held.kind == "cardio" and row["kind"] == "cardio":
            if (held.met, held.compendium_code) != (row["met"], row["compendium_code"]):
                held.met, held.compendium_code = row["met"], row["compendium_code"]
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
    finally:
        dispose()
    print(f"Exercise list synced: {added} added, {total} in all.")


if __name__ == "__main__":
    main()
