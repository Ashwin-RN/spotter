"""export_data: every table the person owns as CSV, deleted and sample rows in, another person's rows out."""

import csv
import io
from datetime import datetime, timezone

import pytest

from app import db, services
from app.models import Base, user_tables

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
FILES = ["account.csv", "lift_sessions.csv", "lift_sets.csv", "bodyweight.csv", "measurements.csv", "goals.csv",
         "saved_foods.csv", "food_entries.csv", "cardio_entries.csv", "README.txt"]


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


def table(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


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
            header = next(csv.reader(io.StringIO(text)))
            assert "user_id" not in header and "dedupe_key" not in header, name
    assert "you_said" in files["README.txt"] and "UTC" in files["README.txt"] and "deleted_at" in files["README.txt"]


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
