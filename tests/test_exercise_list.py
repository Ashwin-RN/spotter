"""The exercise list, package C4a.2 (contract exercise-list.md, R1, R16a, R17, "Muscles and region"): every rule the
owner's reviewed sheet of 2026-11-19 was checked by that needs no outside file, held on app/exercise_rows.py and the
views app/exercises.py builds from it, with the counts.

The check of each lift's muscles against free-exercise-db's JSON needs that file, which is not in the repo: the
C4a.2 builder ran it once, outside the tests. Every oracle below is written out here, never read from the module.
"""

import ast
import collections
import dataclasses
import importlib.util
import re
import tokenize

import pytest

from app import exercise_rows, exercises
from app.config import ROOT

from . import text_rules as tr

ROWS_FILE = ROOT / "app" / "exercise_rows.py"
MOVEMENTS = exercises.MOVEMENTS
LIFT_ROWS = [m for m in MOVEMENTS if m.kind == "lift"]
CARDIO_ROWS = [m for m in MOVEMENTS if m.kind == "cardio"]
BY_NAME = {m.name: m for m in MOVEMENTS}
WORDS = exercise_rows.WORDS


def norm(text: str) -> str:
    """The matcher's key as the sheet's checks hold it: "Name (Equipment)" read as "Equipment Name"; casefold; no
    apostrophes or dots; "&" as "and"; hyphens and slashes as spaces; a plural "es" after ch, sh, ss, x or y off a
    word of more than 4 letters, else a final s (not ss) off a word of 3 letters or more; then no spaces.
    C4c's rules.name_key takes its place here once it exists."""
    t = text.strip()
    m = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", t)
    if m:
        t = f"{m.group(2)} {m.group(1)}"
    t = t.casefold().replace("'", "").replace(chr(0x2019), "").replace(".", "").replace("&", " and ")
    t = re.sub(r"[-/]", " ", t)
    out = []
    for w in t.split():
        if len(w) > 4 and re.search(r"(ch|sh|ss|x|y)es$", w):
            w = w[:-2]
        elif len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return "".join(out)


def reach(text: str) -> set[str]:
    """What a said text reaches under the key: names, aliases (as their row's name) and plain words."""
    k = norm(text)
    found = {m.name for m in MOVEMENTS if norm(m.name) == k}
    found |= {m.name for m in MOVEMENTS for a in m.aliases if norm(a) == k}
    found |= {f"plain word '{w}'" for w in WORDS if norm(w) == k}
    return found


# ---------------------------------------------------------------- the counts

def test_the_counts():
    assert (len(MOVEMENTS), len(LIFT_ROWS), len(CARDIO_ROWS)) == (267, 242, 25)
    assert len({m.family for m in LIFT_ROWS}) == len(exercises.FAMILIES) == 153
    assert collections.Counter(len(v) for v in exercises.FAMILIES.values())[1] == 110  # a single lift
    assert len(WORDS) == 61
    assert len({w["default"] for w in WORDS.values()}) == 46
    names = {norm(m.name) for m in MOVEMENTS}
    assert sum(1 for w in WORDS if norm(w) in names) == 18  # a default's own name, such as "bench press"
    assert len({m.family for m in LIFT_ROWS if m.compare_with}) == 4
    assert sum(len(m.aliases) for m in MOVEMENTS) == len(exercises.ALIASES) == 769
    assert collections.Counter(" ".join(m.measures) for m in LIFT_ROWS) == {
        "weight reps": 228, "weight distance time": 7, "weight time": 4, "reps": 3}
    assert collections.Counter(m.muscle_source for m in LIFT_ROWS)["ours"] == 1
    assert collections.Counter(m.muscle_source for m in LIFT_ROWS)["fedb_near"] == 43


# ---------------------------------------------------------------- the 56 live names

LIVE_LIFTS = [  # the list as it stood before the exercise list: (name, category)
    ("Back Squat", "squat"), ("Front Squat", "squat"), ("Goblet Squat", "squat"), ("Leg Press", "squat"),
    ("Bulgarian Split Squat", "squat"), ("Walking Lunge", "squat"), ("Bodyweight Squat", "squat"),
    ("Step-Up", "squat"), ("Deadlift", "hinge"), ("Romanian Deadlift", "hinge"),
    ("Dumbbell Romanian Deadlift", "hinge"), ("Hip Thrust", "hinge"), ("Glute Bridge", "hinge"),
    ("Kettlebell Swing", "hinge"), ("Bench Press", "push"), ("Incline Dumbbell Press", "push"),
    ("Dumbbell Bench Press", "push"), ("Overhead Press", "push"), ("Dumbbell Shoulder Press", "push"),
    ("Push-Up", "push"), ("Dip", "push"), ("Pull-Up", "pull"), ("Lat Pulldown", "pull"), ("Barbell Row", "pull"),
    ("Dumbbell Row", "pull"), ("Seated Cable Row", "pull"), ("Inverted Row", "pull"), ("Face Pull", "pull"),
    ("Farmer's Carry", "carry"), ("Suitcase Carry", "carry"), ("Plank", "core"), ("Side Plank", "core"),
    ("Dead Bug", "core"), ("Hanging Knee Raise", "core"), ("Pallof Press", "core"), ("Leg Curl", "accessory"),
    ("Leg Extension", "accessory"), ("Calf Raise", "accessory"), ("Dumbbell Curl", "accessory"),
    ("Triceps Pushdown", "accessory"), ("Lateral Raise", "accessory"),
]
LIVE_CARDIO = [  # (name, category, MET, Compendium code)
    ("Treadmill Walk", "walk", 3.8, "17355"), ("Treadmill Run", "run", 9.3, "12050"),
    ("Outdoor Walk", "walk", 3.8, "17190"), ("Outdoor Run", "run", 9.3, "12050"),
    ("Stationary Bike", "cycle", 6.8, "01200"), ("Outdoor Cycling", "cycle", 7.0, "01014"),
    ("Rowing Machine", "row", 5.0, "02071"), ("Elliptical", "machine", 5.0, "02048"),
    ("Stair Climber", "machine", 9.3, "02065"), ("Jump Rope", "other", 11.8, "15551"),
    ("Swimming", "swim", 6.0, "18310"), ("Hiking", "walk", 6.0, "17080"),
    ("Circuit Training", "other", 5.0, "02035"), ("Badminton", "other", 5.5, "15030"),
    ("Football", "other", 7.0, "15610"),
]


def _live_keys() -> dict[str, str]:
    """Migration 0013's LIVE table: each live name's key."""
    path = ROOT / "migrations" / "versions" / "0013_exercise_list.py"
    spec = importlib.util.spec_from_file_location("migration_0013_list", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LIVE


def test_the_56_live_names_keep_their_names_categories_mets_codes_and_keys():
    keys = _live_keys()
    assert len(LIVE_LIFTS) + len(LIVE_CARDIO) == len(keys) == 56
    for name, category in LIVE_LIFTS:
        m = BY_NAME[name]
        assert (m.kind, m.category, m.key, m.met, m.compendium_code) == ("lift", category, keys[name], None, None)
    for name, category, met, code in LIVE_CARDIO:
        m = BY_NAME[name]
        assert (m.kind, m.category, m.key, m.met, m.compendium_code) == ("cardio", category, keys[name], met, code)


# ---------------------------------------------------------------- keys and names

def test_every_key_is_unique_lower_snake_by_the_key_rule_and_fits_the_column():
    keys = [m.key for m in MOVEMENTS]
    assert len(keys) == len(set(keys)) == len(exercises.BY_KEY)
    for m in MOVEMENTS:
        assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", m.key) and len(m.key) <= 64, m.key
        assert exercises.key_of(m.name) == m.key, m.name


def test_every_name_is_unique_also_under_the_matcher_s_key():
    names = [m.name for m in MOVEMENTS]
    assert len(names) == len(set(names)) == len(exercises.BY_NAME)
    under_key = collections.defaultdict(list)
    for name in names:
        under_key[norm(name)].append(name)
    assert [v for v in under_key.values() if len(v) > 1] == []


def test_every_name_s_plural_reaches_its_own_row():
    for m in MOVEMENTS:
        last = m.name.split()[-1]
        if last.endswith("s") and not last.endswith("ss"):
            continue  # already plural: Battle Ropes
        plural = m.name + ("es" if re.search(r"(s|x|ch|sh)$", last) else "s")
        assert norm(plural) == norm(m.name), plural
    assert reach("Pull-Ups") == {"Pull-Up", "plain word 'pull-up'"} and reach("Hack Squats") == {"Hack Squat"}


def test_names_are_plain_and_at_most_64_characters_and_no_text_has_a_dash_but_the_hyphen():
    for m in MOVEMENTS:
        assert len(m.name) <= 64 and re.fullmatch(r"[A-Z0-9][A-Za-z0-9' -]*[A-Za-z0-9]", m.name), m.name
        for text in (m.name, *m.aliases):
            assert "—" not in text and "–" not in text and " - " not in text, text
    for word, w in WORDS.items():
        assert "—" not in word and "–" not in word and " - " not in word, word


# ---------------------------------------------------------------- aliases and plain words

def test_each_alias_names_one_row_and_equals_no_name_no_other_alias_and_no_plain_word():
    names = {norm(m.name) for m in MOVEMENTS}
    words = {norm(w) for w in WORDS}
    owners = collections.defaultdict(list)
    for m in MOVEMENTS:
        for a in m.aliases:
            assert a == a.lower() and a == a.strip(), a
            owners[norm(a)].append(m.name)
    assert {k: v for k, v in owners.items() if len(v) > 1} == {}
    assert set(owners) & names == set() and set(owners) & words == set()
    assert exercises.ALIASES == {a: m.name for m in MOVEMENTS for a in m.aliases}


def test_each_plain_word_has_its_default_first_among_two_or_more_lifts_and_equals_no_name_but_its_default_s():
    names = {norm(m.name): m.name for m in MOVEMENTS}
    assert len({norm(w) for w in WORDS}) == len(WORDS)
    for word, w in WORDS.items():
        cands = w["candidates"]
        assert set(w) == {"default", "candidates"} and word == word.lower(), word
        assert cands[0] == w["default"] and len(cands) >= 2 and len(set(cands)) == len(cands), word
        assert all(c in BY_NAME and BY_NAME[c].kind == "lift" for c in cands), word
        if norm(word) in names:
            assert names[norm(word)] == w["default"], word
    assert WORDS["squat"]["candidates"][:2] == ("Back Squat", "Smith Squat")
    assert WORDS["bench"]["candidates"] == ("Bench Press", "Smith Bench Press")


# ---------------------------------------------------------------- equipment in names

EQUIPMENT_WORDS = [("plate-loaded", "plate_loaded"), ("plate loaded", "plate_loaded"), ("trap bar", "trap_bar"),
                   ("ez bar", "ez_bar"), ("medicine ball", "medicine_ball"), ("dumbbell", "dumbbell"),
                   ("kettlebell", "kettlebell"), ("cable", "cable"), ("smith", "smith"), ("barbell", "barbell"),
                   ("landmine", "landmine"), ("sandbag", "sandbag"), ("banded", "band"), ("band", "band"),
                   ("machine", "machine"), ("sled", "sled"), ("plate", "plate")]  # the first that matches wins
BARBELL_FIVE = ("Bench Press", "Deadlift", "Back Squat", "Overhead Press", "Hip Thrust")
BARBELL_PLAIN = ("Box Squat", "Overhead Squat", "Good Morning", "Rack Pull", "Power Clean", "Clean", "Hang Clean",
                 "Clean and Jerk", "Snatch", "Power Snatch", "Push Press", "Push Jerk", "Split Jerk", "Thruster")
MACHINE_PLAIN = ("Pec Deck", "Reverse Pec Deck", "Hack Squat", "Pendulum Squat", "Belt Squat",
                 "Single-Leg Leg Press", "Single-Leg Leg Extension", "Seated Leg Curl", "Standing Leg Curl",
                 "Seated Calf Raise", "Leg Press Calf Raise", "Reverse Hyperextension", "Close-Grip Lat Pulldown",
                 "Reverse-Grip Lat Pulldown", "Single-Arm Lat Pulldown", "Straight-Arm Pulldown", "Rope Pushdown",
                 "T-Bar Row", "Chest-Supported T-Bar Row", "Assisted Pull-Up", "Assisted Dip", "Wall Ball")
LIVE_NAMES = {n for n, _ in LIVE_LIFTS} | {n for n, *_ in LIVE_CARDIO}


def _said_equipment(name: str):
    low = name.lower()
    return next(((w, e) for w, e in EQUIPMENT_WORDS if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)),
                None)


def test_an_equipment_word_in_a_name_is_its_equipment_and_a_new_lift_names_its_equipment():
    for m in LIFT_ROWS:
        said = _said_equipment(m.name)
        if said:
            assert m.equipment == said[1], m.name
            continue
        if m.name in LIVE_NAMES or m.equipment == "bodyweight":
            continue
        if m.equipment == "barbell":  # decision 7: Barbell is left out of the five, names built on them, and 14 more
            assert any(five in m.name for five in BARBELL_FIVE) or m.name in BARBELL_PLAIN, m.name
        else:  # decision 8: machines named as themselves keep their names
            assert m.name in MACHINE_PLAIN, m.name
    assert all(n in BY_NAME for n in BARBELL_PLAIN + MACHINE_PLAIN)


# ---------------------------------------------------------------- the closed lists and what fits what

LOAD_FITS = {"barbell": {"bar_total"}, "ez_bar": {"bar_total"}, "trap_bar": {"bar_total"},
             "dumbbell": {"per_hand", "single"}, "kettlebell": {"per_hand", "single"}, "cable": {"per_hand", "single"},
             "machine": {"single", "bodyweight"}, "plate_loaded": {"single"}, "smith": {"single"},
             "landmine": {"single"}, "sled": {"single"}, "plate": {"single"}, "medicine_ball": {"single"},
             "sandbag": {"single"}, "other": {"single"}, "bodyweight": {"bodyweight"}, "band": {"band"}}
CARDIO_MEASURES = {"speed", "pace", "incline", "watts", "level", "floors", "rounds"}


def test_the_closed_lists():
    assert exercises.CATEGORIES == ["squat", "hinge", "push", "pull", "carry", "core", "accessory"]
    assert exercises.CARDIO_CATEGORIES == ["walk", "run", "cycle", "row", "swim", "machine", "other"]
    assert set(exercises.EQUIPMENT) == set(LOAD_FITS) and len(exercises.EQUIPMENT) == 17
    assert exercises.LOAD_TYPES == ("bar_total", "per_hand", "single", "bodyweight", "band")
    assert exercises.SIDES == ("both", "each") and exercises.MUSCLE_SOURCES == ("fedb", "fedb_near", "ours")
    assert exercises.MEASURES == ("weight", "reps", "time", "distance", "speed", "pace", "incline", "watts", "level",
                                  "floors", "rounds")


def test_each_lift_s_fields_come_from_their_lists_and_fit_one_another():
    for m in LIFT_ROWS:
        assert m.category in exercises.CATEGORIES and m.equipment in exercises.EQUIPMENT, m.name
        assert m.load_type in exercises.LOAD_TYPES and m.sides in exercises.SIDES, m.name
        assert m.muscle_source in exercises.MUSCLE_SOURCES, m.name
        assert m.load_type in LOAD_FITS[m.equipment], m.name
        ms = m.measures
        assert set(ms) <= set(exercises.MEASURES) and len(set(ms)) == len(ms), m.name
        assert {"reps", "time", "distance"} & set(ms) and not set(ms) & CARDIO_MEASURES, m.name
        assert ("weight" in ms) == (m.load_type != "band"), m.name  # a band set takes no weight
        if m.load_type == "bodyweight" and m.equipment == "machine":
            assert m.name in ("Assisted Pull-Up", "Assisted Dip"), m.name  # decision 9: help as minus
        if re.match(r"(Single|One)-(Leg|Arm)\b", m.name):
            assert m.sides == "each", m.name
        assert m.met is None and m.compendium_code is None, m.name


def test_each_cardio_row_has_no_lift_field_and_records_time_first():
    for m in CARDIO_ROWS:
        assert m.category in exercises.CARDIO_CATEGORIES, m.name
        assert (m.family, m.compare_with, m.equipment, m.load_type, m.sides) == (None,) * 5, m.name
        assert m.measures[0] == "time" and set(m.measures) <= set(exercises.MEASURES), m.name
        assert not {"weight", "reps"} & set(m.measures), m.name


# ---------------------------------------------------------------- families, compare_with and the bar lift

PER_HAND_NO_LINK = {"farmers_carry": "no bar carry on the list",
                    "dumbbell_fly": "no barbell fly",
                    "incline_dumbbell_fly": "no barbell fly",
                    "low_cable_crossover": "no barbell fly",
                    "lateral_raise": "no barbell lateral raise",
                    "dumbbell_front_raise": "no barbell front raise on the list",
                    "dumbbell_rear_delt_fly": "no barbell rear delt fly",
                    "dumbbell_triceps_kickback": "no barbell kickback",
                    "calf_raise": "no barbell calf raise on the list; Smith Calf Raise is a single load"}
LINKS = {"chest_supported_dumbbell_row": "barbell_row", "dumbbell_arnold_press": "overhead_press",
         "dumbbell_hammer_curl": "dumbbell_curl", "incline_dumbbell_curl": "dumbbell_curl"}


def test_a_family_is_keyed_by_its_default_holds_one_lift_a_slot_and_sits_in_one_category():
    for family, members in exercises.FAMILIES.items():
        assert members[0].key == family and members[0].family == family, family  # the default first
        assert all(m.family == family for m in members) and len({m.category for m in members}) == 1, family
        slots = collections.Counter((m.equipment, m.load_type, m.sides) for m in members)
        assert max(slots.values()) == 1, family
        assert sum(m.equipment == "barbell" for m in members) <= 1, family
    assert sorted(m.key for ms in exercises.FAMILIES.values() for m in ms) == sorted(m.key for m in LIFT_ROWS)


def test_compare_with_links_four_families_and_every_other_per_hand_family_without_a_bar_lift_has_its_reason():
    loads = {f: {m.load_type for m in ms} for f, ms in exercises.FAMILIES.items()}
    links = {}
    for family, members in exercises.FAMILIES.items():
        assert len({m.compare_with for m in members}) == 1, family
        if members[0].compare_with:
            links[family] = members[0].compare_with
    assert links == LINKS
    for family, target in links.items():
        assert "per_hand" in loads[family] and "bar_total" not in loads[family], family
        assert target != family and "bar_total" in loads[target], family
    needs = {f for f, ld in loads.items() if "per_hand" in ld and "bar_total" not in ld}
    assert needs - set(links) == set(PER_HAND_NO_LINK) and all(PER_HAND_NO_LINK.values())


def _compared_with(family: str) -> list[str]:
    bars = [m for m in exercises.FAMILIES[family] if m.load_type == "bar_total"]
    return [m.name for m in ([m for m in bars if m.equipment == "barbell"] or bars)]


def test_each_per_hand_family_the_check_reaches_has_one_bar_lift_to_compare_with():
    for family, members in exercises.FAMILIES.items():
        if "per_hand" not in {m.load_type for m in members}:
            continue
        target = family if any(m.load_type == "bar_total" for m in members) else members[0].compare_with
        if target:
            assert len(_compared_with(target)) == 1, family
    assert _compared_with("deadlift") == ["Deadlift"]
    assert [_compared_with(LINKS[f]) for f in LINKS] == [["Barbell Row"], ["Overhead Press"], ["Barbell Curl"],
                                                          ["Barbell Curl"]]


# ---------------------------------------------------------------- muscles and region

def test_every_lift_s_muscles_are_the_17_names_with_a_source_and_no_cardio_row_has_any():
    for m in LIFT_ROWS:
        assert m.primary_muscles and set(m.primary_muscles + m.secondary_muscles) <= set(exercises.MUSCLE_NAMES)
        assert not set(m.primary_muscles) & set(m.secondary_muscles), m.name
        assert (m.source_entry is None) == (m.muscle_source == "ours"), m.name
        assert exercises.MUSCLES[m.name] == {**dict.fromkeys(m.primary_muscles, 1.0),
                                             **dict.fromkeys(m.secondary_muscles, 0.5)}
        assert exercises.FREE_EXERCISE_DB[m.name] == m.source_entry
    for m in CARDIO_ROWS:
        assert (m.primary_muscles, m.secondary_muscles, m.muscle_source, m.source_entry) == ((), (), None, None)
        assert m.name not in exercises.MUSCLES and m.name not in exercises.REGIONS
    assert len(exercises.MUSCLE_NAMES) == 17


def test_dead_hang_s_muscles_are_spotter_s_own_and_suitcase_carry_takes_the_farmer_s_walk():
    hang = BY_NAME["Dead Hang"]
    assert (hang.muscle_source, hang.source_entry, hang.measures) == ("ours", None, ("weight", "time"))
    assert exercises.MUSCLES["Dead Hang"] == {"forearms": 1.0, "lats": 0.5}
    carry = BY_NAME["Suitcase Carry"]
    assert (carry.muscle_source, carry.source_entry) == ("fedb_near", "Farmers_Walk")
    assert exercises.MUSCLES["Suitcase Carry"] == exercises.MUSCLES["Farmer's Carry"]
    assert BY_NAME["Box Jump"].source_entry == "Front_Box_Jump"  # decisions 29 and 30
    assert BY_NAME["Burpee"].source_entry == "Mountain_Climbers"
    assert exercises.MUSCLES["Burpee"] == {"quadriceps": 1.0, "chest": 0.5, "hamstrings": 0.5, "shoulders": 0.5}


def test_the_region_is_the_primary_muscle_s_save_every_carry_and_sled_full_body():
    for m in LIFT_ROWS:
        want = "full_body" if m.category == "carry" else exercises.REGION_OF[m.primary_muscles[0]]
        assert exercises.REGIONS[m.name] == want, m.name
    assert {n for n, r in exercises.REGIONS.items() if r == "full_body"} == {
        "Farmer's Carry", "Suitcase Carry", "Sled Push", "Sled Pull", "Sled Drag"}
    assert exercises.REGIONS["Wall Ball"] == "upper" and exercises.REGIONS["Thruster"] == "upper"
    assert exercises.REGIONS["Split Jerk"] == "lower"


# ---------------------------------------------------------------- cardio: MET and Compendium

def _comments_above_rows() -> dict[str, list[str]]:
    """Each row's comment lines straight above its opening brace in app/exercise_rows.py, by key."""
    text = ROWS_FILE.read_text(encoding="utf-8")
    with ROWS_FILE.open(encoding="utf-8") as f:
        comments = {t.start[0]: t.string for t in tokenize.generate_tokens(f.readline)
                    if t.type == tokenize.COMMENT and t.line.strip() == t.string}
    rows = next(n.value for n in ast.parse(text).body if isinstance(n, ast.AnnAssign) and n.target.id == "ROWS")
    out = {}
    for elt in rows.elts:
        key = ast.literal_eval(elt)["key"]
        line, block = elt.lineno - 1, []
        while line in comments:
            block.insert(0, comments[line])
            line -= 1
        out[key] = block
    return out


def test_every_cardio_met_has_a_five_digit_code_one_met_a_code_and_its_row_s_text_as_a_comment():
    comments = _comments_above_rows()
    met_of = collections.defaultdict(set)
    for m in CARDIO_ROWS:
        assert re.fullmatch(r"\d+\.\d", repr(m.met)) and re.fullmatch(r"\d{5}", m.compendium_code), m.name
        met_of[m.compendium_code].add(m.met)
        assert sum(c.startswith("# Compendium: ") for c in comments[m.key]) == 1, m.name
    assert all(len(v) == 1 for v in met_of.values())
    assert not any(c.startswith("# Compendium: ") for m in LIFT_ROWS for c in comments[m.key])
    assert BY_NAME["Ski Erg"].met == 10.5  # decision 23


def test_a_near_entry_and_spotter_s_own_muscles_each_have_a_note_saying_why():
    comments = _comments_above_rows()
    for m in LIFT_ROWS:
        if m.muscle_source in ("fedb_near", "ours"):
            assert any(not c.startswith("# Compendium: ") for c in comments[m.key]), m.name


def test_no_comment_above_a_row_has_a_dash_between_words_or_an_ai_word():
    for key, block in _comments_above_rows().items():
        for c in block:
            assert "—" not in c and "–" not in c and " - " not in c, (key, c)
            assert tr.check_ai_vocabulary(c) == [], (key, c)


# ---------------------------------------------------------------- Hyrox and the said words

HYROX = ("Ski Erg", "Sled Push", "Sled Pull", "Burpee Broad Jump", "Rowing Machine", "Farmer's Carry",
         "Sandbag Lunge", "Wall Ball", "Treadmill Run", "Outdoor Run")
SAID = [("burpee", "Burpee"), ("burpees", "Burpee"), ("Burpees", "Burpee"), ("full burpee", "Burpee"),
        ("box jump", "Box Jump"), ("box jumps", "Box Jump"), ("front box jump", "Box Jump"),
        ("burpee broad jump", "Burpee Broad Jump"), ("burpee broad jumps", "Burpee Broad Jump"),
        ("burpee jump", "Burpee Broad Jump"), ("hyrox burpees", "Burpee Broad Jump"),
        ("hiit", "HIIT"), ("tabata", "HIIT"), ("interval training", "HIIT")]


def test_hyrox_s_movements_are_on_the_list():
    assert [n for n in HYROX if n not in BY_NAME] == []
    assert BY_NAME["Sled Push"].measures == ("weight", "distance", "time")  # decision 14
    assert BY_NAME["Sandbag Lunge"].sides == "both"


@pytest.mark.parametrize("said, row", SAID)
def test_the_said_words_of_decision_31_reach_their_one_row(said, row):
    assert reach(said) == {row}


def test_no_hiit_word_holds_burpee():
    hiit = BY_NAME["HIIT"]
    assert not any("burpee" in norm(t) for t in (hiit.name, *hiit.aliases))


# ---------------------------------------------------------------- equipment said before a plain word

SAID_EQUIPMENT = [("dumbbell", "dumbbell"), ("db", "dumbbell"), ("barbell", "barbell"), ("bb", "barbell"),
                  ("kettlebell", "kettlebell"), ("kb", "kettlebell"), ("cable", "cable"), ("machine", "machine"),
                  ("smith", "smith"), ("ez bar", "ez_bar"), ("trap bar", "trap_bar"), ("landmine", "landmine"),
                  ("band", "band"), ("plate loaded", "plate_loaded")]
SWEEP_LEFT_OUT = {"cable bench": "Cable Chest Press is a standing press, not a bench",
                  "cable bench press": "Cable Chest Press is a standing press, not a bench",
                  "cable pulldown": "fits every cable pulldown, not only Lat Pulldown",
                  "dumbbell seated row": "not clearly the chest-supported row",
                  "db seated row": "not clearly the chest-supported row",
                  "dumbbell triceps extension": "fits the overhead extension and the skull crusher",
                  "db triceps extension": "fits the overhead extension and the skull crusher",
                  "cable triceps extension": "fits the overhead extension and the pushdown",
                  "ez bar triceps extension": "fits the overhead extension and the skull crusher",
                  "kettlebell dumbbell press": "names two kinds of equipment",
                  "kb dumbbell press": "names two kinds of equipment",
                  "cable dumbbell press": "names two kinds of equipment"}
SWEEP_OTHER_OK = {"machine hip thrust": "Plate-Loaded Hip Thrust", "barbell skull crusher": "EZ Bar Skull Crusher",
                  "landmine row": "T-Bar Row"}


def test_equipment_said_before_a_plain_word_reaches_the_one_lift_true_of_it_and_no_other():
    swept = one = 0
    for word, w in WORDS.items():
        families = {BY_NAME[c].family for c in w["candidates"]}
        for said, equipment in SAID_EQUIPMENT:
            swept += 1
            phrase = f"{said} {word}"
            true = {m.name for f in families for m in exercises.FAMILIES[f] if m.equipment == equipment}
            true |= {m.name for m in LIFT_ROWS if norm(m.name) == norm(phrase)}
            got = reach(phrase)
            if got and SWEEP_OTHER_OK.get(phrase) not in got:
                assert got <= true, phrase
            if len(true) == 1 and phrase not in SWEEP_LEFT_OUT:
                one += 1
                assert got == true, phrase
    assert [p for p in SWEEP_LEFT_OUT if reach(p)] == []
    assert swept == 61 * 14 and one > 0
    assert reach("dumbbell bench") == {"Dumbbell Bench Press"}  # open question 10


# ---------------------------------------------------------------- the views and the rows

def test_each_movement_is_its_row_and_the_views_keep_their_shapes():
    fields = {f.name for f in dataclasses.fields(exercises.Movement)}
    for row, m in zip(exercise_rows.ROWS, MOVEMENTS, strict=True):
        assert set(row) <= fields and {k: getattr(m, k) for k in row} == row, row["key"]
    assert exercises.BY_KEY == {m.key: m for m in MOVEMENTS} and exercises.BY_NAME == BY_NAME
    assert exercises.LIFTS == [(m.name, m.category) for m in LIFT_ROWS]
    assert exercises.CARDIO == [(m.name, m.category, m.met, m.compendium_code) for m in CARDIO_ROWS]
    assert set(exercises.MUSCLES) == set(exercises.REGIONS) == set(exercises.FREE_EXERCISE_DB) == {
        m.name for m in LIFT_ROWS}
    assert exercises.WORDS is WORDS
    assert [r["key"] for r in exercises.rows()] == [m.key for m in MOVEMENTS]
    assert all(set(r) == {"key", "name", "kind", "category", "met", "compendium_code"} for r in exercises.rows())
    with pytest.raises(dataclasses.FrozenInstanceError):
        MOVEMENTS[0].name = "Other"  # type: ignore[misc]


def test_the_rows_file_is_data_only():
    tree = ast.parse(ROWS_FILE.read_text(encoding="utf-8"))
    body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    assert [n.target.id for n in body if isinstance(n, ast.AnnAssign)] == ["ROWS", "WORDS"] and len(body) == 2
    for node in body:
        ast.literal_eval(node.value)  # literals only: nothing to run
