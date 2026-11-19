"""The shared exercise list. The same name every time, so charts and progress line up.

The rows are in app/exercise_rows.py, data only (contract exercise-list.md, R1): one Movement a lift or cardio
activity, and WORDS, the plain words. This module holds the closed lists each field takes and builds every view
from the rows at import: LIFTS, CARDIO, MUSCLES, REGIONS and FREE_EXERCISE_DB keep the shapes their readers use;
BY_KEY, BY_NAME, FAMILIES and ALIASES are new.

Each row has a key (key_of: the name by the rule of contract exercise-list.md), which never changes once a row is
on the list: the database knows a row by it, not by its name. sync(session) matches the table's rows to the list by
key: it adds a listed row the table lacks, brings a held row's name, category, MET and Compendium code up to the
list, retires a row whose key is not listed and brings back one that is. Rows are never deleted, and running it
twice changes nothing. The deploy runs it after the migrations:

    python -m app.exercises
"""

import re
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exercise_rows import ROWS, WORDS  # WORDS is read here as exercises.WORDS
from app.models import Exercise

# The closed lists, one a field.
KINDS = ("lift", "cardio")
CATEGORIES = ["squat", "hinge", "push", "pull", "carry", "core", "accessory"]
CARDIO_CATEGORIES = ["walk", "run", "cycle", "row", "swim", "machine", "other"]
EQUIPMENT = ("barbell", "dumbbell", "kettlebell", "cable", "machine", "plate_loaded", "smith", "trap_bar", "ez_bar",
             "landmine", "band", "bodyweight", "plate", "sled", "medicine_ball", "sandbag", "other")
# What a lift's logged weight means: bar and plates, one of two equal weights (one a hand), one weight as said, the
# added weight (help as minus on the two assisted lifts), or none (a band, stored as 0 kg).
LOAD_TYPES = ("bar_total", "per_hand", "single", "bodyweight", "band")
SIDES = ("both", "each")  # each: one side at a time, the reps, time or distance a side
MEASURES = ("weight", "reps", "time", "distance", "speed", "pace", "incline", "watts", "level", "floors", "rounds")
MUSCLE_SOURCES = ("fedb", "fedb_near", "ours")  # the lift's own entry, another movement's near it, or none

# The muscles each lift works and its body region (contract data-on-request.md, decision 17; exercise-list.md,
# "Muscles and region"), from free-exercise-db (yuhonas/free-exercise-db on GitHub, The Unlicense): each primary
# muscle 1 a set, each secondary 0.5 (R13). Code only, never a column: sync and list_exercises do not read them. The
# region is worked from the lift's primary muscle (REGION_OF), save every lift in the carry category, the carries
# and the sleds, which are full_body.
MUSCLE_NAMES = ("abdominals", "abductors", "adductors", "biceps", "calves", "chest", "forearms", "glutes", "hamstrings",
                "lats", "lower back", "middle back", "neck", "quadriceps", "shoulders", "traps", "triceps")
REGION_NAMES = ("upper", "lower", "core", "full_body")
REGION_OF = {**dict.fromkeys(("quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors", "lower back"),
                             "lower"),
             **dict.fromkeys(("chest", "shoulders", "lats", "middle back", "traps", "biceps", "triceps", "forearms",
                              "neck"), "upper"),
             "abdominals": "core"}
FULL_BODY_CATEGORY = "carry"  # a carry or a sled is grip, legs and trunk at once


@dataclass(frozen=True)
class Movement:
    """One row of the list. measures is what an entry records; aliases are other names true of this row alone.
    A lift's own: family (its family's default lift's key), compare_with (the family the per-hand check reaches
    through, or None), equipment, load_type, sides, and its muscles with their source and free-exercise-db entry.
    A cardio row's own: met and compendium_code."""
    key: str
    name: str
    kind: str
    category: str
    measures: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    family: str | None = None
    compare_with: str | None = None
    equipment: str | None = None
    load_type: str | None = None
    sides: str | None = None
    primary_muscles: tuple[str, ...] = ()
    secondary_muscles: tuple[str, ...] = ()
    muscle_source: str | None = None
    source_entry: str | None = None
    met: float | None = None
    compendium_code: str | None = None


MOVEMENTS: tuple[Movement, ...] = tuple(Movement(**row) for row in ROWS)  # in the list's order
BY_KEY: dict[str, Movement] = {m.key: m for m in MOVEMENTS}
BY_NAME: dict[str, Movement] = {m.name: m for m in MOVEMENTS}
# An alias as written: its row's name.
ALIASES: dict[str, str] = {alias: m.name for m in MOVEMENTS for alias in m.aliases}
_LIFT_ROWS = [m for m in MOVEMENTS if m.kind == "lift"]
# A family's key, its default lift's key: its lifts, the default first, then the rest in the list's order.
FAMILIES: dict[str, tuple[Movement, ...]] = {
    family: tuple(sorted((m for m in _LIFT_ROWS if m.family == family), key=lambda m, f=family: m.key != f))
    for family in dict.fromkeys(m.family for m in _LIFT_ROWS)}

# (name, category)
LIFTS: list[tuple[str, str]] = [(m.name, m.category) for m in _LIFT_ROWS]
# Cardio: (name, category, MET, Compendium code), kind "cardio". Every MET and code is a row of the 2024 Adult
# Compendium of Physical Activities (Herrmann SD et al., pacompendium.com, CC BY-NC-ND 4.0); app/exercise_rows.py
# quotes each row's description.
CARDIO: list[tuple[str, str, float, str]] = [(m.name, m.category, m.met, m.compendium_code)
                                             for m in MOVEMENTS if m.kind == "cardio"]
COMPENDIUM = "2024 Adult Compendium of Physical Activities"

FREE_EXERCISE_DB: dict[str, str | None] = {m.name: m.source_entry for m in _LIFT_ROWS}  # None for Dead Hang, ours
MUSCLES: dict[str, dict[str, float]] = {
    m.name: {**dict.fromkeys(m.primary_muscles, 1.0), **dict.fromkeys(m.secondary_muscles, 0.5)} for m in _LIFT_ROWS}
REGIONS: dict[str, str] = {
    m.name: "full_body" if m.category == FULL_BODY_CATEGORY else REGION_OF[m.primary_muscles[0]] for m in _LIFT_ROWS}


def key_of(name: str) -> str:
    """A row's key from its name: lowercased, apostrophes dropped, "&" as "and", and each run of hyphens, slashes,
    brackets, commas, full stops and spaces as one underscore, none at either end. "Farmer's Carry" is farmers_carry.
    migrations/versions/0013_exercise_list.py holds the same rule."""
    key = name.lower().replace("'", "").replace(chr(0x2019), "").replace("&", "and")
    return re.sub(r"[-/(),.\s]+", "_", key).strip("_")


def rows() -> list[dict]:
    """Every exercise on the list, as column values: its key as the row holds it, and the five columns."""
    return [{"key": m.key, "name": m.name, "kind": m.kind, "category": m.category, "met": m.met,
             "compendium_code": m.compendium_code} for m in MOVEMENTS]


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
