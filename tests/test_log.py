"""get_log: the days, newest first, with sessions and their bests, the weigh-in and the measurements."""

from datetime import datetime, timedelta, timezone

import pytest

from app import db, services, users
from app.metrics import epley


@pytest.fixture
def user(make_user):
    return make_user()


def day(user, offset):
    return (users.today_for(user) + timedelta(days=offset)).isoformat()


def squat(user, kg, on=None):
    data = {"sets": [{"exercise": "Back Squat", "weight_kg": kg, "reps": 5}]}
    if on is not None:
        data["date"] = on
    r = services.log_lifts(user.id, data, "claude")
    assert "error" not in r, r
    return r


def test_one_day_by_default_with_the_contracts_shape(user):
    s = squat(user, 60)
    w = services.log_bodyweight(user.id, {"kg": 80}, "web")
    m = services.log_measurement(user.id, {"kind": "waist", "value": 84}, "web")
    r = services.get_log(user.id)
    assert set(r) == {"from", "to", "days", "totals", "includes_sample", "summary", "links"}
    assert r["includes_sample"] is False
    assert r["from"] == r["to"] == day(user, 0) and len(r["days"]) == 1
    only = r["days"][0]
    assert set(only) == {"date", "sessions", "weigh_in", "measurements", "food", "cardio"}
    assert [x["id"] for x in only["sessions"]] == [s["id"]] and only["sessions"][0]["sets"][0]["weight_kg"] == 60
    assert only["sessions"][0]["new_bests"] == []
    assert only["weigh_in"]["id"] == w["id"] and [x["id"] for x in only["measurements"]] == [m["id"]]
    assert r["totals"] == {"sessions": 1, "weigh_ins": 1, "measurements": 1, "food_entries": 0, "cardio_entries": 0}
    assert r["summary"] == "1 day, 1 session, 1 weigh-in, 1 measurement, 0 food entries, 0 cardio sessions."
    assert r["links"]["day"].endswith(f"/day/{day(user, 0)}") and r["links"]["progress"].endswith("/progress")


def test_every_day_in_the_range_newest_first_empty_ones_included(user):
    squat(user, 60, day(user, -2))
    services.log_measurement(user.id, {"kind": "steps", "value": 9000, "date": day(user, -2)}, "claude")
    services.log_measurement(user.id, {"kind": "waist", "value": 84, "date": day(user, -2)}, "claude")
    r = services.get_log(user.id, days=3)
    assert [d["date"] for d in r["days"]] == [day(user, 0), day(user, -1), day(user, -2)]
    assert r["from"] == day(user, -2) and r["to"] == day(user, 0)
    assert r["days"][0] == {"date": day(user, 0), "sessions": [], "weigh_in": None, "measurements": [],
                            "food": {"meals": {"breakfast": [], "lunch": [], "dinner": [], "snack": []}, "total_kcal": 0, "estimated_kcal": 0, "estimated_share_pct": 0, "target_kcal": None, "remaining_kcal": None, "net_kcal": None}, "cardio": []}
    assert [m["kind"] for m in r["days"][2]["measurements"]] == ["waist", "steps"]  # the kinds' own order
    assert r["summary"] == "3 days, 1 session, 0 weigh-ins, 2 measurements, 0 food entries, 0 cardio sessions."


def test_a_date_ends_the_range(user):
    squat(user, 60, day(user, -5))
    squat(user, 62.5, day(user, -1))
    r = services.get_log(user.id, date=day(user, -4), days=2)
    assert [d["date"] for d in r["days"]] == [day(user, -4), day(user, -5)]
    assert r["totals"]["sessions"] == 1 and r["links"]["day"].endswith(f"/day/{day(user, -4)}")


def test_each_session_carries_its_own_new_bests_the_same_as_get_progress(user):
    squat(user, 60, day(user, -3))
    second = squat(user, 65, day(user, -2))
    squat(user, 62.5, day(user, -1))
    r = services.get_log(user.id, days=4)
    by_id = {x["id"]: x["new_bests"] for d in r["days"] for x in d["sessions"]}
    assert {(b["kind"], b["value"], b["previous"]) for b in by_id[second["id"]]} == {
        ("estimated_max", epley(65, 5), epley(60, 5)), ("weight", 65, 60)}
    assert all(b["text"] for b in by_id[second["id"]])
    assert sum(len(v) for v in by_id.values()) == 2
    progress = services.get_progress(user.id, "Back Squat")["lift"]["bests"]
    assert {(b["kind"], b["value"]) for b in progress} == {(b["kind"], b["value"]) for b in by_id[second["id"]]}
    assert {(b["kind"], b["value"]) for b in second["new_bests"]} == {(b["kind"], b["value"]) for b in by_id[second["id"]]}


def test_deleted_entries_are_left_out(user):
    s = squat(user, 60)
    services.delete_entry(user.id, "lift_session", s["id"])
    assert services.get_log(user.id)["totals"]["sessions"] == 0


@pytest.mark.parametrize("bad", [0, 15, -1, 1.5, "two", True, [1], 10 ** 400])
def test_days_is_one_to_fourteen(user, bad):
    r = services.get_log(user.id, days=bad)
    assert r["error"] == "invalid_field" and "1 to 14" in r["fix"]


def test_days_at_the_edges_and_as_text(user):
    assert len(services.get_log(user.id, days=14)["days"]) == 14
    assert len(services.get_log(user.id, days="3")["days"]) == 3
    assert len(services.get_log(user.id, days=None)["days"]) == 1


def test_a_date_after_today_or_not_a_date_is_refused(user):
    assert services.get_log(user.id, date=day(user, 1))["error"] == "date_in_future"
    assert services.get_log(user.id, date="soon")["error"] == "invalid_date"
    assert services.get_log(987654)["error"] == "no_account"


def test_after_a_change_of_zone_a_later_dated_day_appears_on_top(user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc))
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "claude")
    later = squat(user, 100)                                              # dated 11-10
    services.log_measurement(user.id, {"kind": "waist", "value": 84}, "web")
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "claude")   # today 11-09
    now = squat(user, 90)                                                 # dated 11-09
    r = services.get_log(user.id, days=2)
    assert [d["date"] for d in r["days"]] == ["2026-11-10", "2026-11-09", "2026-11-08"]
    assert [x["id"] for x in r["days"][0]["sessions"]] == [later["id"]] and r["days"][0]["measurements"]
    assert [x["id"] for x in r["days"][1]["sessions"]] == [now["id"]]
    assert r["from"] == "2026-11-08" and r["to"] == "2026-11-09"
    assert r["summary"] == "3 days, 2 sessions, 0 weigh-ins, 1 measurement, 0 food entries, 0 cardio sessions."
    # With a date given, the range is exactly the range asked for.
    asked = services.get_log(user.id, date="2026-11-09", days=2)
    assert [d["date"] for d in asked["days"]] == ["2026-11-09", "2026-11-08"]



def test_each_day_has_its_food_by_meal_and_its_cardio(make_user):
    person = make_user(calorie_target_kcal=2000)
    services.log_bodyweight(person.id, {"kg": 80}, "web")
    eaten = services.log_food(person.id, {"entries": [
        {"description": "oats", "kcal": 300, "meal": "breakfast"},
        {"description": "dal", "kcal": 200, "meal": "lunch", "estimated": True},
        {"description": "rice", "kcal": 250, "meal": "lunch"}], "date": day(person, -1)}, "claude")
    walk = services.log_cardio(person.id, {"exercise": "Outdoor Walk", "minutes": 30, "distance_km": 3,
                                           "date": day(person, -1)}, "claude")
    r = services.get_log(person.id, days=2)
    yesterday = r["days"][1]
    meals = yesterday["food"]["meals"]
    assert [e["description"] for e in meals["breakfast"]] == ["oats"]
    assert [e["description"] for e in meals["lunch"]] == ["dal", "rice"] and meals["dinner"] == meals["snack"] == []
    assert yesterday["food"]["total_kcal"] == 750 and yesterday["food"]["estimated_kcal"] == 200
    assert yesterday["food"]["remaining_kcal"] == 1250 and yesterday["food"]["estimated_share_pct"] == 27
    assert eaten["day"] == {k: yesterday["food"][k] for k in eaten["day"]}
    [moved] = yesterday["cardio"]
    assert moved["id"] == walk["id"] and moved["exercise"] == "Outdoor Walk"
    assert moved["pace"]["min_per_km"] == "10:00" and moved["estimate"] == walk["estimate"]
    assert "machine_kcal" in moved
    assert r["totals"]["food_entries"] == 3 and r["totals"]["cardio_entries"] == 1
    assert "3 food entries, 1 cardio session." in r["summary"]
    assert r["days"][0]["food"]["total_kcal"] == 0


# ---------------------------------------------------------------- stage 5: sample rows say so (rule 16)

def test_get_log_says_when_sample_rows_are_shown_and_only_then(user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc))
    assert "error" not in services.add_sample_data(user.id, "web")
    r = services.get_log(user.id, days=3)
    assert r["includes_sample"] is True and "Remove sample data button on Settings" in r["note"]
    # Days before the sample window show none of it.
    earlier = services.get_log(user.id, date="2026-10-13", days=14)
    assert earlier["includes_sample"] is False and "note" not in earlier
    # A deleted sample row is not shown, so it does not count.
    services.remove_sample_data(user.id, "web")
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    plain = services.get_log(user.id)
    assert plain["includes_sample"] is False and "note" not in plain
