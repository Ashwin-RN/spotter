"""The exercise list's key, and sets of time and distance: the columns and the rules (deploy 1).

Revision: 0013
Follows: 0012

Contract exercise-list.md, "Migrations 0013 and 0014":
- exercise.key, VARCHAR(64): added nullable, filled for every row by the key rule (key_of, with the 56 live names
  written out in LIVE), then NOT NULL and unique (uq_exercise_key). A row not among the 56 takes the key its name
  gives, and the run prints that key, never more.
- exercise.retired, BOOLEAN, NOT NULL, server default false: a row no longer on the list. Rows are never deleted.
- lift_set.time_s (INTEGER) and lift_set.distance_m (FLOAT), both nullable; lift_set.reps becomes nullable.
- The rules on lift_set, each a CHECK named ck_lift_set_<rule> (RULES): weight_kg widens to -1,000 to 1,000 (a minus
  weight is help on an assisted lift), dropped and added again; time_s 1 to 100,000; distance_m above 0, at most
  100,000; measure: a set holds reps, a time or a distance. reps keeps 0012's 1 to 1,000, which a null passes. The
  rules' text lives in this file only (hardening.md D18), and is wider than every range the services hold.

The rows are proved first. Before any change, every row's key is worked out, and two rows of one key stop the upgrade
with one line a key, its count, never a name: nothing is changed. The downgrade stops the same way on a set with no
reps or with a minus weight. Otherwise it drops the four columns and the new rules, makes reps NOT NULL again and puts
back 0012's weight_kg rule. It leaves every exercise row in place: older code lists the rows the table holds. Offline
(--sql) there is no read and no count: the fill is the 56 updates by name.

On SQLite, exercise and lift_set are rebuilt by batch, with foreign keys off (migrations/env.py), carrying every rule
of 0012 on lift_set, its unique and its two indexes. On Postgres, ALTER TABLE makes each change.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

import re
import sys
from collections import Counter

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# The 56 names on the list at 0012, each with its key: what the deploy's live rows hold.
LIVE = {
    "Back Squat": "back_squat",
    "Front Squat": "front_squat",
    "Goblet Squat": "goblet_squat",
    "Leg Press": "leg_press",
    "Bulgarian Split Squat": "bulgarian_split_squat",
    "Walking Lunge": "walking_lunge",
    "Bodyweight Squat": "bodyweight_squat",
    "Step-Up": "step_up",
    "Deadlift": "deadlift",
    "Romanian Deadlift": "romanian_deadlift",
    "Dumbbell Romanian Deadlift": "dumbbell_romanian_deadlift",
    "Hip Thrust": "hip_thrust",
    "Glute Bridge": "glute_bridge",
    "Kettlebell Swing": "kettlebell_swing",
    "Bench Press": "bench_press",
    "Incline Dumbbell Press": "incline_dumbbell_press",
    "Dumbbell Bench Press": "dumbbell_bench_press",
    "Overhead Press": "overhead_press",
    "Dumbbell Shoulder Press": "dumbbell_shoulder_press",
    "Push-Up": "push_up",
    "Dip": "dip",
    "Pull-Up": "pull_up",
    "Lat Pulldown": "lat_pulldown",
    "Barbell Row": "barbell_row",
    "Dumbbell Row": "dumbbell_row",
    "Seated Cable Row": "seated_cable_row",
    "Inverted Row": "inverted_row",
    "Face Pull": "face_pull",
    "Farmer's Carry": "farmers_carry",
    "Suitcase Carry": "suitcase_carry",
    "Plank": "plank",
    "Side Plank": "side_plank",
    "Dead Bug": "dead_bug",
    "Hanging Knee Raise": "hanging_knee_raise",
    "Pallof Press": "pallof_press",
    "Leg Curl": "leg_curl",
    "Leg Extension": "leg_extension",
    "Calf Raise": "calf_raise",
    "Dumbbell Curl": "dumbbell_curl",
    "Triceps Pushdown": "triceps_pushdown",
    "Lateral Raise": "lateral_raise",
    "Treadmill Walk": "treadmill_walk",
    "Treadmill Run": "treadmill_run",
    "Outdoor Walk": "outdoor_walk",
    "Outdoor Run": "outdoor_run",
    "Stationary Bike": "stationary_bike",
    "Outdoor Cycling": "outdoor_cycling",
    "Rowing Machine": "rowing_machine",
    "Elliptical": "elliptical",
    "Stair Climber": "stair_climber",
    "Jump Rope": "jump_rope",
    "Swimming": "swimming",
    "Hiking": "hiking",
    "Circuit Training": "circuit_training",
    "Badminton": "badminton",
    "Football": "football",
}

# (rule, condition): the CHECK named ck_lift_set_<rule> after this revision. weight_kg replaces 0012's.
RULES = (
    ("weight_kg", "weight_kg >= -1000 AND weight_kg <= 1000"),
    ("time_s", "time_s >= 1 AND time_s <= 100000"),
    ("distance_m", "distance_m > 0 AND distance_m <= 100000"),
    ("measure", "reps IS NOT NULL OR time_s IS NOT NULL OR distance_m IS NOT NULL"),
)
WEIGHT_AT_0012 = "weight_kg >= 0 AND weight_kg <= 1000"  # 0012's ck_lift_set_weight_kg, put back by the downgrade
UQ_KEY = "uq_exercise_key"
KEY_LENGTH = 64

exercise = sa.table("exercise", sa.column("id", sa.Integer), sa.column("name", sa.String), sa.column("key", sa.String))
lift_set = sa.table("lift_set", sa.column("reps", sa.Integer), sa.column("weight_kg", sa.Float))


def key_of(name: str) -> str:
    """The key rule, as app/exercises.py holds it: lowercased, apostrophes dropped, "&" as "and", each run of hyphens,
    slashes, brackets, commas, full stops and spaces as one underscore, none at either end."""
    key = name.lower().replace("'", "").replace("’", "").replace("&", "and")
    return re.sub(r"[-/(),.\s]+", "_", key).strip("_")


def _name(rule: str) -> str:
    return f"ck_lift_set_{rule}"


def _online() -> bool:
    return not op.get_context().as_sql


def _count(statement) -> int:
    return op.get_bind().execute(statement).scalar() or 0


def _stop(lines: list[str]) -> None:
    """Stop the run with these lines, before any change: the database stays where it was."""
    sys.exit("\n".join(lines))


def _others() -> list[tuple[int, str]]:
    """(id, key) for each row not among the 56, after checking that no two rows take one key. Nothing is changed."""
    held = op.get_bind().execute(sa.select(exercise.c.id, exercise.c.name).order_by(exercise.c.id)).all()
    keys = {row_id: LIVE.get(name) or key_of(name) for row_id, name in held}
    counts = Counter(keys.values())
    twins = sorted(key for key, n in counts.items() if n > 1)
    if twins:
        _stop([f"0013 stopped: exercise key {key} {counts[key]} rows. Nothing was changed." for key in twins])
    return [(row_id, keys[row_id]) for row_id, name in held if name not in LIVE]


def upgrade() -> None:
    others = _others() if _online() else []
    op.add_column("exercise", sa.Column("key", sa.String(KEY_LENGTH), nullable=True))
    op.add_column("exercise", sa.Column("retired", sa.Boolean(), server_default=sa.false(), nullable=False))
    for name, key in LIVE.items():
        op.execute(exercise.update().where(exercise.c.name == name).values(key=key))
    for row_id, key in others:
        op.execute(exercise.update().where(exercise.c.id == row_id).values(key=key))
        print(f"0013 keyed an exercise not on the list: {key}")
    with op.batch_alter_table("exercise") as batch:
        batch.alter_column("key", existing_type=sa.String(KEY_LENGTH), nullable=False)
        batch.create_unique_constraint(op.f(UQ_KEY), ["key"])

    op.add_column("lift_set", sa.Column("time_s", sa.Integer(), nullable=True))
    op.add_column("lift_set", sa.Column("distance_m", sa.Float(), nullable=True))
    with op.batch_alter_table("lift_set") as batch:
        batch.alter_column("reps", existing_type=sa.Integer(), nullable=True)
        batch.drop_constraint(op.f(_name("weight_kg")), type_="check")
        for rule, condition in RULES:
            batch.create_check_constraint(op.f(_name(rule)), condition)


def _downgrade_stops() -> list[str]:
    lines = []
    for column, where in (("reps", lift_set.c.reps.is_(None)), ("weight_kg", lift_set.c.weight_kg < 0)):
        n = _count(sa.select(sa.func.count()).select_from(lift_set).where(where))
        if n:
            lines.append(f"0013 down stopped: lift_set {column} {n} rows. Nothing was changed.")
    return lines


def downgrade() -> None:
    if _online():
        stops = _downgrade_stops()
        if stops:
            _stop(stops)
    with op.batch_alter_table("lift_set") as batch:
        for rule, _ in reversed(RULES):
            batch.drop_constraint(op.f(_name(rule)), type_="check")
        batch.create_check_constraint(op.f(_name("weight_kg")), WEIGHT_AT_0012)
        batch.alter_column("reps", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("distance_m")
        batch.drop_column("time_s")
    with op.batch_alter_table("exercise") as batch:
        batch.drop_constraint(op.f(UQ_KEY), type_="unique")
        batch.drop_column("retired")
        batch.drop_column("key")
