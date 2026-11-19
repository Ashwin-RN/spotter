"""list_exercises, package C4d (contract exercise-list.md, R8 and decision 16): the function takes the account. Bare, it
gives the person's own logged lifts and activities, then the usual lifts (each plain word's default), then every cardio
activity, each once, with count and note. A kind or a category gives every row of it. A search gives up to 10 rows
whose key holds the search's key, by name, then alias, then a plain word's candidates, and count says how many
matched. No row shows an alias. Every listing is in the list's own order, not by id (the note of 2026-11-19, (b)).
The exercises resource is every row, whatever the person logged. The pages' slugs reach every lift, a retired one too.

Every date is a fixed today through db.utcnow, never the real date. Each answer's size is printed for the hand-over.
"""

import json
import re
from datetime import datetime, timezone

import anyio
import pytest
from sqlalchemy import delete, select, update

from app import db, exercises, mcp_server, rules, services, web
from app.mcp_schemas import ExerciseItem, ExercisesResult
from app.models import CardioEntry, Exercise, LiftSession, LiftSet
from app.services import account

from . import text_rules as tr
from .conftest import rpc
from .test_calllog import MARKER, rows as call_rows
from .test_idempotency import writer  # noqa: F401 - a fixture
from .test_mcp import failed, ok, read_resource, tool_list

NOW = datetime(2026, 11, 20, 8, 30, tzinfo=timezone.utc)
ORDER = [m.name for m in exercises.MOVEMENTS]  # the list's own order
LIFTS = [m.name for m in exercises.MOVEMENTS if m.kind == "lift"]
CARDIO = [m.name for m in exercises.MOVEMENTS if m.kind == "cardio"]
DEFAULTS = [n for n in LIFTS if n in {w["default"] for w in exercises.WORDS.values()}]
NOTE = ("The person's logged exercises, the usual lifts and every cardio activity, of 267. Send search with a word, "
        "such as row, for the other lifts.")
ALIASES = {a for m in exercises.MOVEMENTS for a in m.aliases}


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def uid(make_user) -> int:
    return make_user(time_zone="UTC").id


def names(result: dict) -> list[str]:
    assert "error" not in result, result
    return [e["name"] for e in result["exercises"]]


def lift(uid: int, name: str, days_ago: int = 1, **fields) -> int:
    """One set of a lift by its exact name, in a new session; its session id."""
    when = (NOW.date().toordinal() - days_ago)
    sent = {"exercise": name, "exact": True, **({"weight_kg": 60, "reps": 5} if not fields else fields)}
    r = services.log_lifts(uid, {"date": datetime.fromordinal(when).date().isoformat(), "new_session": True,
                                 "sets": [sent]}, "claude")
    assert "error" not in r, r
    return r["session"]["id"]


def cardio(uid: int, name: str) -> None:
    r = services.log_cardio(uid, {"exercise": name, "minutes": 20}, "claude")
    assert "error" not in r, r


def size(result: dict) -> int:
    """The answer's size as the MCP wrapper counts it (result_bytes): compact JSON in UTF-8 bytes."""
    return mcp_server._json_text(result)[1]


def reversed_ids() -> None:
    """The list again with every id in the reverse of the list's order, so id order and list order differ."""
    with db.session_scope() as s:
        s.execute(delete(Exercise))
        for row in reversed(exercises.rows()):
            s.add(Exercise(**row))
            s.flush()
    with db.session_scope() as s:
        held = {e.name: e.id for e in s.scalars(select(Exercise))}
    assert held["Bench Press"] > held["Treadmill Walk"]  # the first lift now holds a later id than the last cardio row


# ---------------------------------------------------------------- the bare call

def test_a_new_account_sees_the_46_usual_lifts_then_the_25_cardio_activities_with_the_count_and_the_note(uid):
    r = services.list_exercises(uid)
    assert len(DEFAULTS) == 46 and len(CARDIO) == 25
    assert names(r) == DEFAULTS + CARDIO
    assert r["count"] == 267 == len(ORDER) and r["note"] == NOTE
    assert len(set(names(r))) == len(names(r)) == 71
    ExercisesResult.model_validate(r)
    assert tr.check(r["note"], tr.CLAUDE) == []


def test_the_person_s_own_lifts_and_activities_come_first_in_list_order_then_the_rest_each_once(uid, make_user):
    for name in ("Hack Squat", "Bench Press", "Smith Squat"):
        lift(uid, name)
    cardio(uid, "Rowing Machine")
    other = make_user(time_zone="UTC").id
    lift(other, "Pendulum Squat")
    cardio(other, "Elliptical")
    r = services.list_exercises(uid)
    own = [n for n in ORDER if n in {"Hack Squat", "Bench Press", "Smith Squat", "Rowing Machine"}]
    assert names(r)[:4] == own == ["Bench Press", "Smith Squat", "Hack Squat", "Rowing Machine"]
    assert names(r)[4:] == [n for n in DEFAULTS + CARDIO if n not in own]
    assert "Pendulum Squat" not in names(r)  # another account's lift is never in it
    assert names(r).count("Elliptical") == 1 and names(r).index("Elliptical") > names(r).index("Back Squat")
    assert len(names(r)) == 73 and r["count"] == 267 and r["note"] == NOTE
    assert names(services.list_exercises(other))[:2] == ["Pendulum Squat", "Elliptical"]


def test_a_deleted_session_or_cardio_entry_counts_as_not_logged_and_a_retired_lift_is_left_out(uid):
    gone = lift(uid, "Hack Squat")
    lift(uid, "Pendulum Squat")
    cardio(uid, "Rowing Machine")
    with db.session_scope() as s:
        s.execute(update(LiftSession).where(LiftSession.id == gone).values(deleted_at=NOW))
        s.execute(update(CardioEntry).where(CardioEntry.user_id == uid).values(deleted_at=NOW))
        s.scalar(select(Exercise).where(Exercise.name == "Pendulum Squat")).retired = True
    r = services.list_exercises(uid)
    assert "Hack Squat" not in names(r) and "Pendulum Squat" not in names(r)
    assert names(r) == DEFAULTS + CARDIO and r["count"] == 266  # Rowing Machine in its place among the cardio


def test_every_listing_is_in_the_list_s_order_whatever_the_ids(uid):
    reversed_ids()
    lift(uid, "Hack Squat")
    lift(uid, "Bench Press")
    bare = services.list_exercises(uid)
    assert names(bare)[:2] == ["Bench Press", "Hack Squat"]
    assert names(bare)[2:] == [n for n in DEFAULTS + CARDIO if n != "Bench Press"]
    assert names(services.list_exercises(uid, "lift")) == LIFTS
    assert names(services.list_exercises(uid, category="squat")) == [
        m.name for m in exercises.MOVEMENTS if m.category == "squat"]
    assert names(services.all_exercises()) == ORDER
    assert names(services.list_exercises(uid, search="hip thrust")) == [
        "Hip Thrust", "Smith Hip Thrust", "Plate-Loaded Hip Thrust", "Dumbbell Hip Thrust"]


def test_a_lift_row_says_its_equipment_how_its_weight_is_logged_and_what_a_set_records(uid):
    every = {e["name"]: e for e in services.all_exercises()["exercises"]}
    assert every["Bench Press"] == {"name": "Bench Press", "kind": "lift", "category": "push", "met": None,
                                    "compendium_code": None, "equipment": "barbell", "weight": "bar and plates",
                                    "records": "reps"}
    assert (every["Dumbbell Bench Press"]["weight"], every["Dumbbell Bench Press"]["equipment"]) == (
        "one hand's weight", "dumbbell")
    assert [every[n]["equipment"] for n in ("Plate-Loaded Chest Press", "Smith Squat", "Trap Bar Deadlift",
                                            "EZ Bar Curl", "Wall Ball")] == [
        "plate-loaded machine", "Smith machine", "trap bar", "EZ bar", "medicine ball"]
    assert every["Machine Chest Press"]["weight"] == "one weight"
    assert (every["Pull-Up"]["weight"], every["Assisted Pull-Up"]["weight"], every["Assisted Dip"]["weight"]) == (
        "added weight", "help as a minus weight", "help as a minus weight")
    band = next(m.name for m in exercises.MOVEMENTS if m.load_type == "band")
    assert every[band]["weight"] == "none"
    assert every["Plank"]["records"] == "time" and every["Farmer's Carry"]["records"] == "distance and time"
    walk = every["Treadmill Walk"]
    assert walk["met"] and walk["compendium_code"] and (walk["equipment"], walk["weight"], walk["records"]) == (
        None, None, None)
    for m in exercises.MOVEMENTS:  # every lift has all three, from the list's own row
        e = every[m.name]
        if m.kind == "lift":
            assert e["equipment"] == account.EQUIPMENT_WORDS[m.equipment], m.name
            assert e["records"] == rules.records(m.name), m.name
            assert e["weight"] in set(account.WEIGHT_WORDS.values()) | {account.HELP_WORDS}, m.name
    assert set(account.EQUIPMENT_WORDS) == set(exercises.EQUIPMENT)  # a plain word for every value the list takes
    assert all("_" not in words for words in account.EQUIPMENT_WORDS.values())
    for words in {*account.WEIGHT_WORDS.values(), account.HELP_WORDS, *account.EQUIPMENT_WORDS.values()}:
        assert tr.check(words, tr.CLAUDE) == [], words


# ---------------------------------------------------------------- a kind, a category, a search

def test_a_kind_or_a_category_alone_gives_every_row_of_it_with_its_count_and_no_note(uid):
    lift(uid, "Hack Squat")
    lifts = services.list_exercises(uid, "lift")
    assert names(lifts) == LIFTS and lifts["count"] == 242 and "note" not in lifts
    assert names(services.list_exercises(uid, "cardio")) == CARDIO
    squat = services.list_exercises(uid, None, "squat")
    assert names(squat) == [m.name for m in exercises.MOVEMENTS if m.category == "squat"]
    assert squat["count"] == len(names(squat)) > 10 and "Hack Squat" in names(squat)  # "which squat lifts": all
    assert names(services.list_exercises(uid, "cardio", "run")) == [
        m.name for m in exercises.MOVEMENTS if m.kind == "cardio" and m.category == "run"]
    bare = ["Hack Squat"] + DEFAULTS + CARDIO
    assert names(services.list_exercises(uid, "", "", "")) == bare  # blank is left out
    assert names(services.list_exercises(uid, search="   ")) == bare


KEYS = {m.name: (rules.name_key(m.name), [rules.name_key(a) for a in m.aliases]) for m in exercises.MOVEMENTS}
WORD_KEYS = {word: rules.name_key(word) for word in exercises.WORDS}


def word_starts(text: str) -> list[str]:
    """The text's key from each of its own words on: a search that begins one of them begins a word of the text."""
    words = [k for k in (rules.name_key(w) for w in re.split(r"[\s/-]+", text)) if k]
    return ["".join(words[i:]) for i in range(len(words))]


def expected(text: str, kind: str | None = None, category: str | None = None) -> list[str]:
    """The search rule written out on its own: the row the matcher reaches by the text, then names that hold the
    key, then aliases, word starts before mid-word in each, then words' candidates; each in the list's order."""
    key = rules.name_key(text)
    rows = [m for m in exercises.MOVEMENTS if (not kind or m.kind == kind) and (not category or m.category == category)]
    hit = rules.find_exercise(text, [m.name for m in rows])[0]

    def starts_first(group: list, texts) -> list:
        begins = [m for m in group if any(x.startswith(key) for t in texts(m) for x in word_starts(t))]
        return [m.name for m in begins] + [m.name for m in group if m not in begins]

    by_name = starts_first([m for m in rows if key in KEYS[m.name][0]], lambda m: [m.name])
    by_alias = starts_first([m for m in rows if any(key in a for a in KEYS[m.name][1])], lambda m: m.aliases)
    words = {c for word, w in exercises.WORDS.items() if key in WORD_KEYS[word] for c in w["candidates"]}
    by_word = [m.name for m in rows if m.name in words]
    return list(dict.fromkeys(([hit] if hit else []) + by_name + by_alias + by_word))


def test_a_search_finds_names_then_aliases_then_a_word_s_candidates_at_most_10_with_the_count(uid):
    row = services.list_exercises(uid, search="row")
    held = [m.name for m in exercises.MOVEMENTS if "row" in rules.name_key(m.name)]
    assert row["count"] == len(expected("row")) > 10 and names(row) == held[:10]
    assert "note" not in row
    assert names(services.list_exercises(uid, search="Hip Thrusts")) == [  # plural and case forgiven
        "Hip Thrust", "Smith Hip Thrust", "Plate-Loaded Hip Thrust", "Dumbbell Hip Thrust"]
    smith = services.list_exercises(uid, search="smith")
    assert smith["count"] == len(expected("smith")) > 10 and len(names(smith)) == 10
    assert all("Smith" in n for n in names(smith))
    # An alias: "db bench" is Dumbbell Bench Press's, and the row shows the lift's own name, never the alias.
    db_bench = services.list_exercises(uid, search="db bench")
    assert names(db_bench) == ["Dumbbell Bench Press"] and db_bench["count"] == 1
    # Names first, aliases after: Bench Press, first on the list, is reached by its alias "barbell chest press" only,
    # so it comes after every name that holds chest press.
    chest = names(services.list_exercises(uid, search="chest press"))
    by_name = [n for n in ORDER if "chestpress" in rules.name_key(n)]
    assert chest[:len(by_name)] == by_name and "Bench Press" in chest[len(by_name):]
    assert ORDER.index("Bench Press") < ORDER.index(by_name[0])
    nothing = services.list_exercises(uid, search="zorbinator")
    assert names(nothing) == [] and nothing["count"] == 0


def test_a_search_matches_the_rule_over_every_name_alias_word_and_filter(uid):
    texts = {t for m in exercises.MOVEMENTS for t in (m.name, *m.aliases)} | set(exercises.WORDS)
    texts |= {"a", "db", "bb", "press", "curl", "machine", "cable", "dumbbell", "raise", "walk", "erg", "jump"}
    for text in sorted(texts):
        r = services.list_exercises(uid, search=text)
        want = expected(text)
        assert r["count"] == len(want) and names(r) == want[:10], text
    for kind, category, text in (("cardio", None, "row"), ("lift", None, "rowing"), (None, "pull", "row"),
                                 ("lift", "push", "press"), (None, "run", "run"), ("cardio", None, "Squat (Barbell)"),
                                 ("lift", None, "Squat (Barbell)"), (None, "core", "Lat Pulldown (Cable)")):
        r = services.list_exercises(uid, kind, category, text)
        want = expected(text, kind, category)
        assert r["count"] == len(want) and names(r) == want[:10], (kind, category, text)
    assert names(services.list_exercises(uid, "cardio", None, "row")) == ["Rowing Machine"]
    assert names(services.list_exercises(uid, "lift", None, "rowing")) == []


def test_a_plain_word_s_candidates_come_after_names_and_aliases(uid, monkeypatch):
    # On today's list every candidate of a word holds the word in its name or an alias, so the third group adds no
    # row; a word that names lifts by other words shows it does.
    monkeypatch.setattr(account, "_WORD_KEYS", account._WORD_KEYS + (
        (rules.name_key("zz press"), ("Back Squat", "Plank")),))
    r = services.list_exercises(uid, search="zz press")
    assert names(r) == ["Back Squat", "Plank"] and r["count"] == 2
    assert exercises.BY_NAME["Plank"].category == "core" != exercises.BY_NAME["Back Squat"].category
    assert names(services.list_exercises(uid, None, "core", "zz press")) == ["Plank"]  # a filter still holds
    assert names(services.list_exercises(uid, "cardio", None, "zz press")) == []
    found = names(services.list_exercises(uid, search="z"))
    assert found == list(dict.fromkeys(expected("z") + ["Back Squat", "Plank"]))[:10]


def test_every_word_s_candidates_are_found_by_its_own_word(uid):
    for word, w in exercises.WORDS.items():
        r = services.list_exercises(uid, search=word)
        found = set(names(r))
        assert r["count"] >= len(w["candidates"]), word
        if r["count"] <= 10:
            assert set(w["candidates"]) <= found, (word, found)


def test_a_search_reaches_first_what_the_matcher_reaches(uid):
    # The import's names part says to search each name an export holds and send the name it gives: so a name, an
    # alias, a plural or a "Name (Equipment)" form, as an export writes it, puts its own lift at row 1, as
    # rules.find_exercise reads it outside log_lifts. A word keeps R8's order and shows its default, however many
    # match.
    missed = []
    for m in exercises.MOVEMENTS:
        for text in (m.name, *m.aliases):
            if names(services.list_exercises(uid, search=text))[:1] != [m.name]:
                missed.append((text, m.name))
    assert missed == []
    for word, w in exercises.WORDS.items():
        assert w["default"] in names(services.list_exercises(uid, search=word)), word
    squat = services.list_exercises(uid, search="Squat (Barbell)")
    assert names(squat) == ["Back Squat"] and squat["count"] == 1  # no row's key holds squatbarbell
    assert names(services.list_exercises(uid, search="Lat Pulldown (Cable)"))[0] == "Lat Pulldown"
    assert names(services.list_exercises(uid, search="Hip Thrusts"))[0] == "Hip Thrust"
    assert services.list_exercises(uid, "cardio", None, "Squat (Barbell)")["count"] == 0
    # A plain word reaches no one row: "squat" lists its names in the list's order, Back Squat first by that alone.
    assert names(services.list_exercises(uid, search="squat")) == expected("squat")[:10]
    # The matcher's row goes first where the name group alone would put it second, and is counted once:
    # Bent-Over Dumbbell Row comes before Dumbbell Row on the list, and Single-Arm Cable Row before Seated Cable Row.
    for text, first in (("Dumbbell Row", "Dumbbell Row"), ("cable row", "Seated Cable Row"), ("Clean", "Clean")):
        r = services.list_exercises(uid, search=text)
        assert names(r)[0] == first and names(r).count(first) == 1, (text, names(r))
        assert r["count"] == len(expected(text)), text
    assert ORDER.index("Bent-Over Dumbbell Row") < ORDER.index("Dumbbell Row")


def test_a_word_start_comes_before_a_mid_word_match_and_count_is_unchanged(uid):
    tops = {text: names(services.list_exercises(uid, search=text))[:3] for text in ("run", "lat", "chin", "ab")}
    assert tops["run"] == ["Treadmill Run", "Outdoor Run", "Curved Treadmill Run"]  # before Crunch
    assert tops["lat"][0] == "Lat Pulldown"  # before Plate-Loaded Chest Press
    assert tops["chin"][0] == "Chin-Up"  # before Machine Chest Press
    assert tops["ab"][0] == "Ab Wheel Rollout"  # before Cable Chest Press
    run = services.list_exercises(uid, search="run")
    assert "Crunch" in names(run) and run["count"] == sum(
        "run" in KEYS[n][0] or any("run" in a for a in KEYS[n][1]) for n in ORDER)
    # Word starts are each word's own key, never a place in the joined key: "Plate-Loaded" begins plate and loaded.
    assert word_starts("Plate-Loaded Lat Pulldown") == [
        "plateloadedlatpulldown", "loadedlatpulldown", "latpulldown", "pulldown"]
    assert account._starts("Plate-Loaded Lat Pulldown") == tuple(word_starts("Plate-Loaded Lat Pulldown"))


def test_no_row_ever_shows_an_alias(uid):
    lift(uid, "Hack Squat")
    seen = set()
    for search in [None, "row", "db", "bench", "smith", "curl", "bb", "machine", "press"] + sorted(ALIASES)[:60]:
        seen |= set(names(services.list_exercises(uid, search=search)))
    seen |= set(names(services.all_exercises()))
    assert seen <= set(ORDER) and not (seen & (ALIASES - set(ORDER)))


def test_a_search_that_is_not_text_too_long_or_holds_no_letter_is_refused(uid):
    assert services.list_exercises(uid, search=5)["error"] == "invalid_field"
    long = services.list_exercises(uid, search="x" * (rules.NAME_MAX + 1))
    assert long["error"] == "invalid_field" and str(rules.NAME_MAX) in long["message"]
    assert names(services.list_exercises(uid, search="x" * rules.NAME_MAX)) == []
    for sent in (".", "'", "-/-"):
        r = services.list_exercises(uid, search=sent)
        assert r == {"error": "invalid_field", "message": f"search '{sent}' holds no letter or number.",
                     "fix": "Send a word, such as row, or leave search out."}, r
        assert tr.check(r["message"], tr.MESSAGE) == [] and tr.check(r["fix"], tr.MESSAGE) == []
    swim = services.list_exercises(uid, kind="swim")
    assert swim["error"] == "invalid_field" and swim["fix"] == "Use one of: lift, cardio, or leave it out."
    legs = services.list_exercises(uid, category="legs", search="row")
    assert legs["error"] == "invalid_field" and legs["fix"].endswith(", or leave it out.")
    assert "for all" not in swim["fix"] + legs["fix"]


def test_an_account_that_is_gone_is_no_account(uid):
    assert services.list_exercises(uid + 1000)["error"] == "no_account"


# ---------------------------------------------------------------- the resource and the pages

def test_all_exercises_is_every_row_not_retired_and_retired_too_for_the_pages(uid):
    lift(uid, "Hack Squat")
    every = services.all_exercises()
    assert names(every) == ORDER and len(ORDER) == 267
    assert names(services.all_exercises("lift")) == LIFTS and names(services.all_exercises("cardio")) == CARDIO
    with db.session_scope() as s:
        s.scalar(select(Exercise).where(Exercise.name == "Pendulum Squat")).retired = True
    assert "Pendulum Squat" not in names(services.all_exercises())
    assert names(services.all_exercises("lift", retired=True)) == LIFTS
    assert "Pendulum Squat" not in names(services.list_exercises(uid, "lift"))
    assert services.list_exercises(uid, search="pendulum")["count"] == 0


def test_the_pages_slugs_reach_a_lift_never_logged_and_a_retired_one(uid):
    with db.session_scope() as s:
        s.scalar(select(Exercise).where(Exercise.name == "Pendulum Squat")).retired = True
    slugs = anyio.run(web.lift_names)
    assert slugs["hack-squat"] == "Hack Squat" and slugs["pendulum-squat"] == "Pendulum Squat"
    assert set(slugs.values()) == set(LIFTS)
    assert anyio.run(web.cardio_names) == CARDIO
    assert anyio.run(web.exercise_names, "lift") == [n for n in LIFTS if n != "Pendulum Squat"]


# ---------------------------------------------------------------- through /mcp

def test_through_mcp_the_function_takes_search_and_answers_as_the_service_does(writer):
    http, token, account_id = writer
    tool = {t["name"]: t for t in tool_list(http, token)}["list_exercises"]
    assert tool["description"] == (
        "Exercise names, with each lift's equipment and how its weight is logged. Left bare: the person's own, the "
        "usual lifts and every cardio activity, and how many Spotter knows. search finds the rest. log_lifts also "
        "takes the person's own words; every other function takes a name as written here.")
    props = tool["inputSchema"]["properties"]
    assert list(props) == ["kind", "category", "search"]
    assert props["kind"]["description"] == "lift or cardio: every exercise of that kind."
    assert props["category"]["description"] == (
        "For lifts: squat, hinge, push, pull, carry, core or accessory. For cardio: walk, run, cycle, row, swim, "
        "machine or other. Every exercise in it.")
    assert props["search"]["description"] == (
        "A word or two, such as row or hip thrust: up to 10 lifts and activities whose name, other names or plain "
        "words hold it.")
    out = tool["outputSchema"]
    item = out["$defs"]["ExerciseItem"]["properties"]
    assert item["equipment"]["description"] == "A lift's equipment, such as dumbbell or machine; null for cardio."
    assert item["weight"]["description"] == (
        "How a lift's weight is logged: bar and plates, one hand's weight, one weight, added weight (help as minus), "
        "or none; null for cardio.")
    assert item["records"]["description"] == "What a set records: reps, time, or distance and time; for cardio, null."
    assert out["properties"]["count"]["description"] == (
        "How many exercises match, or with nothing sent, how many Spotter knows.")
    texts = [tool["description"], *(props[k]["description"] for k in ("kind", "category", "search"))] + [
        f["description"] for f in (*item.values(), *out["properties"].values())]
    for text in texts:
        assert tr.check(text, tr.CLAUDE) == [], text
    assert ok(http, token, "list_exercises") == services.list_exercises(account_id)
    assert ok(http, token, "list_exercises", {"search": "row"}) == services.list_exercises(account_id, search="row")
    assert ok(http, token, "list_exercises", {"kind": "lift", "search": "hip thrust"})["count"] == 4
    assert failed(http, token, "list_exercises", {"search": "."})["error"] == "invalid_field"


def test_the_search_s_text_never_reaches_the_call_log(writer):
    http, token, _ = writer
    ok(http, token, "list_exercises", {"search": MARKER})
    row = [r for r in call_rows() if r.function == "list_exercises"][-1]
    assert row.tried == "search" and MARKER not in json.dumps(
        {c.name: str(getattr(row, c.key)) for c in row.__table__.columns})


def test_the_resource_is_every_row_whatever_the_person_logged(writer):
    http, token, account_id = writer
    ok(http, token, "log_lifts", {"sets": [{"exercise": "Hack Squat", "weight_kg": 100, "reps": 8}]})
    listed = {r["uri"]: r for r in rpc(http, "resources/list", token=token).json()["result"]["resources"]}
    assert listed["spotter://exercises"]["description"] == (
        "Every exercise Spotter knows, as JSON, with its equipment and how its weight is logged.")
    assert tr.check(listed["spotter://exercises"]["description"], tr.CLAUDE) == []
    body = json.loads(read_resource(http, token, "spotter://exercises")["text"])
    assert body == services.all_exercises() and names(body) == ORDER
    ExercisesResult.model_validate(body)
    assert all(ExerciseItem.model_validate(e) for e in body["exercises"])


# ---------------------------------------------------------------- sizes, for the hand-over

def test_each_answer_s_size_is_printed_for_the_hand_over(uid, make_user, capsys):
    empty = services.list_exercises(uid)
    full = make_user(time_zone="UTC").id
    for m in exercises.MOVEMENTS:  # every lift and activity logged: the bare call's ceiling
        if m.kind == "cardio":
            cardio(full, m.name)
    with db.session_scope() as s:
        ids = {e.name: e.id for e in s.scalars(select(Exercise))}
    with db.session_scope() as s:
        session = LiftSession(user_id=full, date=NOW.date(), logged_by="claude", dedupe_key="size")
        s.add(session)
        s.flush()
        s.add_all(LiftSet(session_id=session.id, exercise_id=ids[n], set_number=1, weight_kg=0, reps=5)
                  for n in LIFTS)
    every = services.list_exercises(full)
    assert len(every["exercises"]) == 267 and names(every) == ORDER
    sizes = {"bare, empty account": size(empty), "bare, every row logged": size(every),
             "search row": size(services.list_exercises(uid, search="row")),
             "search hip thrust": size(services.list_exercises(uid, search="hip thrust")),
             "kind lift": size(services.list_exercises(uid, "lift")),
             "category squat": size(services.list_exercises(uid, category="squat")),
             "the resource": len(json.dumps(services.all_exercises(), separators=(",", ":")).encode("utf-8"))}
    with capsys.disabled():
        print("\nlist_exercises sizes, bytes: " + ", ".join(f"{k} {v}" for k, v in sizes.items()))
    assert sizes["bare, empty account"] < sizes["bare, every row logged"]
