"""The shared exercise list. The same name every time, so charts and progress line up.

sync(session) adds any exercise missing from the database, matched by name, and changes nothing else:
running it twice changes nothing. The deploy runs it after the migrations:

    python -m app.exercises
"""

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

# Cardio arrives in stage 4: (name, category, met, compendium_code), with kind "cardio".
CARDIO: list[tuple[str, str, float, str]] = []


def rows() -> list[dict]:
    """Every exercise on the list, as column values."""
    lifts = [{"name": name, "kind": "lift", "category": category, "met": None, "compendium_code": None}
             for name, category in LIFTS]
    cardio = [{"name": name, "kind": "cardio", "category": category, "met": met, "compendium_code": code}
              for name, category, met, code in CARDIO]
    return lifts + cardio


def sync(session: Session) -> int:
    """Add every listed exercise the database does not have yet, by name. Returns how many were added.
    An existing row is never changed. The caller commits."""
    existing = set(session.scalars(select(Exercise.name)))
    added = [Exercise(**row) for row in rows() if row["name"] not in existing]
    session.add_all(added)
    session.flush()
    return len(added)


def main() -> None:
    from app.db import dispose, session_scope

    try:
        with session_scope() as session:
            added = sync(session)
            total = len(session.scalars(select(Exercise.id)).all())
    finally:
        dispose()
    print(f"Exercise list synced: {added} added, {total} in all.")


if __name__ == "__main__":
    main()
