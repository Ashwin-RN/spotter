"""export_data: every table the person owns as CSV, deleted and sample rows in, another person's rows out."""

import csv
import io
import re
from datetime import datetime, timezone

import pytest

from app import db, services
from app.models import Base, user_tables

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
BOM = "\ufeff"
FILES = ["account.csv", "lift_sessions.csv", "lift_sets.csv", "bodyweight.csv", "measurements.csv", "goals.csv",
         "saved_foods.csv", "food_entries.csv", "cardio_entries.csv", "README.txt"]


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


def table(text: str) -> list[dict]:
    """A CSV file's rows. Every CSV starts with the byte-order mark (D16), which is not part of the header."""
    assert text.startswith(BOM)
    return list(csv.DictReader(io.StringIO(text.removeprefix(BOM))))


def one_of_everything(user_id: int) -> dict:
    ids = {}
    ids["session"] = services.log_lifts(user_id, {"notes": "heavy, then light", "sets": [
        {"exercise": "Back Squat", "weight_kg": 100, "reps": 5},
        {"exercise": "Bench Press", "weight_kg": 60, "reps": 8}]}, "claude")["id"]
    ids["weigh_in"] = services.log_bodyweight(user_id, {"kg": 80.5}, "web")["id"]
    ids["gone"] = services.log_bodyweight(user_id, {"kg": 81, "date": "2026-11-01"}, "web")["id"]
    services.delete_entry(user_id, "bodyweight", ids["gone"])
    services.log_measurement(user_id, {"kind": "waist", "value": 84}, "web")
    ids["roti"] = services.save_food(user_id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120},
                                     "web")["id"]
    ids["food"] = services.log_food(user_id, {"entries": [{"saved_food_id": ids["roti"], "servings": 2},
                                                          {"description": "dal, with \"ghee\"", "kcal": 180,
                                                           "estimated": True}]}, "claude")["ids"]
    services.log_cardio(user_id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5}, "web")
    services.log_measurement(user_id, {"kind": "waist", "value": 84.5, "date": "2026-11-03"}, "web")
    services.set_goal(user_id, {"kind": "measurement", "measurement_kind": "waist", "target_value": 80,
                                "target_date": "2027-03-31"}, "claude")
    return ids


def test_every_table_and_every_file(make_user):
    user = make_user(email="asha@example.com", name="Asha", time_zone="Asia/Kolkata", calorie_target_kcal=2200)
    ids = one_of_everything(user.id)
    r = services.export_data(user.id)
    assert list(r["files"]) == FILES and list(r["rows"]) == FILES[:-1]
    assert r["rows"] == {"account.csv": 1, "lift_sessions.csv": 1, "lift_sets.csv": 2, "bodyweight.csv": 2,
                         "measurements.csv": 2, "goals.csv": 1, "saved_foods.csv": 1, "food_entries.csv": 2,
                         "cardio_entries.csv": 1}
    assert r["summary"] == "13 rows in 9 CSV files, deleted and sample rows included, times in UTC."
    account = table(r["files"]["account.csv"])
    assert account == [{"account_id": str(user.id), "email": "asha@example.com", "name": "Asha",
                        "time_zone": "Asia/Kolkata", "calorie_target_kcal": "2200", "target_sessions_per_week": "",
                        "target_cardio_minutes_per_week": "", "add_back_exercise_kcal": "false", "claude_can": "log",
                        "created_at": account[0]["created_at"]}]
    sets = table(r["files"]["lift_sets.csv"])
    assert sets == [{"session_id": str(ids["session"]), "exercise": "Back Squat", "set_number": "1",
                     "weight_kg": "100", "reps": "5"},
                    {"session_id": str(ids["session"]), "exercise": "Bench Press", "set_number": "1",
                     "weight_kg": "60", "reps": "8"}]
    weigh_ins = table(r["files"]["bodyweight.csv"])
    assert {w["kg"] for w in weigh_ins} == {"80.5", "81"}           # a fraction stays, a whole number loses .0
    assert [f["kcal"] for f in table(r["files"]["food_entries.csv"])] == ["240", "180"]
    assert table(r["files"]["lift_sessions.csv"])[0]["notes"] == "heavy, then light"   # quoted, read back whole


def test_deleted_rows_are_in_with_their_time_and_times_are_utc(make_user):
    user = make_user()
    ids = one_of_everything(user.id)
    weigh_ins = {row["id"]: row for row in table(services.export_data(user.id)["files"]["bodyweight.csv"])}
    assert weigh_ins[str(ids["gone"])]["deleted_at"] == "2026-11-10T08:30:00+00:00"
    assert weigh_ins[str(ids["weigh_in"])]["deleted_at"] == ""
    assert weigh_ins[str(ids["weigh_in"])]["created_at"] == "2026-11-10T08:30:00+00:00"
    assert set(weigh_ins[str(ids["weigh_in"])]) == {"id", "date", "kg", "logged_by", "created_at", "updated_at",
                                                    "deleted_at", "is_sample"}


def test_names_not_ids_and_the_tags_and_sample_marks(make_user):
    user = make_user()
    ids = one_of_everything(user.id)
    files = services.export_data(user.id)["files"]
    food = table(files["food_entries.csv"])
    assert [(f["description"], f["source"], f["saved_food_id"], f["saved_food"], f["is_sample"]) for f in food] == [
        ("Roti", "saved_food", str(ids["roti"]), "Roti", "false"),
        ('dal, with "ghee"', "claude_estimate", "", "", "false")]
    assert table(files["cardio_entries.csv"])[0]["exercise"] == "Outdoor Run"
    goal = table(files["goals.csv"])[0]
    assert goal["kind"] == "measurement" and goal["measurement_kind"] == "waist" and goal["exercise"] == ""
    assert "exercise_id" not in goal and "user_id" not in goal
    saved = table(files["saved_foods.csv"])[0]
    assert saved["name"] == "Roti" and "name_key" not in saved
    for name, text in files.items():
        if name.endswith(".csv"):
            header = next(csv.reader(io.StringIO(text.removeprefix(BOM))))
            assert "user_id" not in header and "dedupe_key" not in header, name
    assert "you_said" in files["README.txt"] and "UTC" in files["README.txt"] and "deleted_at" in files["README.txt"]


def test_imported_rows_say_import_and_the_readme_says_what_it_means(make_user):
    """Import pass B: every row import_entries writes is marked import in logged_by, and the README says so."""
    user = make_user(time_zone="Asia/Kolkata")
    services.log_bodyweight(user.id, {"kg": 80, "date": "2026-11-01"}, "web")
    r = services.import_entries(user.id, {"dry_run": False, "rows": [
        {"kind": "bodyweight", "date": "2026-11-02", "weight": 80.5},
        {"kind": "measurement", "date": "2026-11-02", "measurement_kind": "waist", "value": 84},
        {"kind": "lift_session", "date": "2026-11-03", "sets": [{"exercise": "Back Squat", "weight": 100, "reps": 5}]},
        {"kind": "food_entry", "date": "2026-11-03", "entries": [{"description": "Oats", "kcal": 350, "meal": "breakfast"}]},
        {"kind": "cardio_entry", "date": "2026-11-04", "exercise": "Outdoor Run", "minutes": 30}],
        "weight_unit": "kg", "length_unit": "cm"}, "claude")
    assert r["row_outcomes"]["written"] == 5, r
    files = services.export_data(user.id)["files"]
    by = {name: [row["logged_by"] for row in table(files[name])]
          for name in ("lift_sessions.csv", "bodyweight.csv", "measurements.csv", "food_entries.csv",
                       "cardio_entries.csv")}
    assert by == {"lift_sessions.csv": ["import"], "bodyweight.csv": ["web", "import"], "measurements.csv": ["import"],
                  "food_entries.csv": ["import"], "cardio_entries.csv": ["import"]}
    readme = " ".join(files["README.txt"].split())
    assert "logged_by, in lift_sessions, bodyweight, measurements, food_entries and cardio_entries" in readme
    assert "import (brought over from another app's export with import_entries)" in readme
    # The whole sentence, who logged a row in each of its three words (after fix round 3).
    assert ("logged_by, in lift_sessions, bodyweight, measurements, food_entries and cardio_entries, says how a row "
            "came in: web (on Spotter's own pages), claude (through the assistant) or import (brought over from "
            "another app's export with import_entries).") in readme
    for name in ("goals.csv", "saved_foods.csv", "lift_sets.csv"):  # the README names only the files that have it
        assert "logged_by" not in next(csv.reader(io.StringIO(files[name].removeprefix(BOM)))), name


def test_sample_rows_are_marked(make_user, monkeypatch):
    user = make_user()
    services.add_sample_data(user.id, "web")
    r = services.export_data(user.id)
    assert r["rows"]["food_entries.csv"] == 240 and r["rows"]["goals.csv"] == 3
    for name in ("lift_sessions.csv", "bodyweight.csv", "goals.csv", "saved_foods.csv", "food_entries.csv",
                 "cardio_entries.csv", "measurements.csv"):
        assert {row["is_sample"] for row in table(r["files"][name])} == {"true"}, name


def test_another_persons_rows_are_out(make_user):
    a, b = make_user(), make_user()
    one_of_everything(b.id)
    mine = services.export_data(a.id)
    assert all(count == 0 for name, count in mine["rows"].items() if name != "account.csv")
    assert all(len(table(mine["files"][name])) == 0 for name in FILES[1:-1])
    assert table(mine["files"]["account.csv"])[0]["account_id"] == str(a.id)
    assert services.export_data(b.id)["rows"]["food_entries.csv"] == 2


def test_every_table_the_person_owns_has_a_file():
    from app.services.export import TABLES
    exported = {model.__tablename__ for model, _ in TABLES.values()} | {"lift_set"}
    owned = set(user_tables()) - {"call_log"}   # the call log holds no content
    assert owned <= exported
    assert "lift_set" in Base.metadata.tables


def test_no_account():
    assert services.export_data(987654)["error"] == "no_account"


# ---------------------------------------------------------------- T6: the files a spreadsheet opens

UNIT_PARTS = {"kg", "kcal", "km", "kmh", "pct", "bpm", "minutes", "grams"}
# Number columns with no unit to name: ids, counts, and a value whose unit is in its row's unit column.
UNITLESS = {"id", "account_id", "session_id", "saved_food_id", "usda_fdc_id", "set_number", "reps", "servings",
            "target_sessions_per_week", "value", "start_value", "target_value"}
NUMBER = re.compile(r"-?\d+(\.\d+)?")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00")


def hostile(user_id: int) -> None:
    """Text a spreadsheet would run as a formula, in every kind of text cell the person writes."""
    services.log_lifts(user_id, {"notes": "=SUM(A1:A9)", "sets": [{"exercise": "Deadlift", "weight_kg": 100,
                                                                    "reps": 5}]}, "claude")
    services.log_food(user_id, {"entries": [{"description": "+cmd", "kcal": 10},
                                            {"description": "-2 dal", "kcal": 20}]}, "claude")
    services.save_food(user_id, {"name": "@home roti", "serving_label": "=1 roti", "kcal_per_serving": 90,
                                 "grams_per_serving": 40}, "web")
    services.log_cardio(user_id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "avg_hr": 150,
                                  "notes": "-fast"}, "web")
    services.set_goal(user_id, {"kind": "bodyweight", "target_value": 75, "target_date": "2027-03-31",
                                "text": "@ the wedding"}, "web")


def test_every_csv_is_utf8_with_a_byte_order_mark_and_readme_is_plain(make_user):
    user = make_user(name="Asha")
    one_of_everything(user.id)
    files = services.export_data(user.id)["files"]
    for name, text in files.items():
        if name.endswith(".csv"):
            assert text.startswith(BOM) and text.count(BOM) == 1, name
            assert text.encode("utf-8").startswith(b"\xef\xbb\xbf"), name
    assert not files["README.txt"].startswith(BOM)


def test_headers_are_snake_case_with_a_unit_on_every_fixed_unit_number(make_user):
    user = make_user(calorie_target_kcal=2200, target_sessions_per_week=3, target_cardio_minutes_per_week=150)
    one_of_everything(user.id)
    hostile(user.id)
    services.add_sample_data(make_user().id, "web")   # another person's: no bearing on this export
    files = services.export_data(user.id)["files"]
    checked = set()
    for name, text in files.items():
        if not name.endswith(".csv"):
            continue
        rows = table(text)
        header = list(rows[0])
        assert all(re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", h) for h in header), (name, header)
        for column in header:
            cells = [r[column] for r in rows if r[column] != ""]
            if not cells or not all(NUMBER.fullmatch(c) for c in cells):
                continue
            checked.add(column)
            assert column in UNITLESS or set(column.split("_")) & UNIT_PARTS, (name, column)
    assert {"weight_kg", "kg", "kcal", "grams_per_serving", "minutes", "distance_km", "speed_kmh", "avg_hr_bpm",
            "estimated_kcal", "estimate_weight_kg", "calorie_target_kcal", "target_cardio_minutes_per_week"} <= checked
    assert "avg_hr" not in table(files["cardio_entries.csv"])[0]


def test_a_notes_column_wherever_a_table_has_notes(make_user):
    from app.services.export import TABLES
    user = make_user()
    one_of_everything(user.id)
    files = services.export_data(user.id)["files"]
    with_notes = [name for name, (model, _) in TABLES.items() if "notes" in model.__table__.c]
    assert sorted(with_notes) == ["cardio_entries.csv", "lift_sessions.csv"]
    for name in with_notes:
        assert "notes" in next(csv.reader(io.StringIO(files[name].removeprefix(BOM)))), name


def test_cells_carry_no_unit_no_formula_and_no_point_zero(make_user):
    user = make_user()
    one_of_everything(user.id)
    hostile(user.id)
    files = services.export_data(user.id)["files"]
    cells = [(name, c, v) for name, text in files.items() if name.endswith(".csv")
             for row in table(text) for c, v in row.items()]
    for name, column, value in cells:
        assert not re.fullmatch(r"-?\d+(\.\d+)?\s*(kg|kcal|km|km/h|cm|bpm|min|g|ml|h|%)", value), (name, column, value)
        assert not re.fullmatch(r"-?\d+\.0+", value), (name, column, value)   # 100, not 100.0
        if not NUMBER.fullmatch(value):
            assert value[:1] not in ("=", "+", "-", "@"), (name, column, value)
    food = {f["description"] for f in table(files["food_entries.csv"])}
    assert {"'+cmd", "'-2 dal"} <= food                     # shown as text, never run as a formula
    saved = table(files["saved_foods.csv"])[-1]
    assert (saved["name"], saved["serving_label"]) == ("'@home roti", "'=1 roti")
    assert table(files["lift_sessions.csv"])[-1]["notes"] == "'=SUM(A1:A9)"
    assert table(files["cardio_entries.csv"])[-1]["notes"] == "'-fast"
    assert table(files["goals.csv"])[-1]["text"] == "'@ the wedding"
    # What the person typed is stored as typed: the apostrophe is the file's, not the data's.
    assert services.get_log(user.id)["days"][0]["sessions"][-1]["notes"] == "=SUM(A1:A9)"


def test_dates_are_iso_and_times_are_utc_with_the_offset(make_user):
    user = make_user()
    one_of_everything(user.id)
    hostile(user.id)
    goal = services.get_goals(user.id)["goals"][0]["goal_id"]
    services.set_goal(user.id, {"goal_id": goal, "status": "dropped"}, "web")   # a closed_at to read
    files = services.export_data(user.id)["files"]
    seen = set()
    for name, text in files.items():
        if not name.endswith(".csv"):
            continue
        for row in table(text):
            for column, value in row.items():
                if value == "":
                    continue
                if column == "date" or column.endswith("_date"):
                    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), (name, column, value)
                    seen.add(column)
                elif column.endswith("_at"):
                    assert TIME.fullmatch(value), (name, column, value)
                    seen.add(column)
    assert {"date", "start_date", "target_date", "created_at", "updated_at", "deleted_at", "closed_at"} <= seen
