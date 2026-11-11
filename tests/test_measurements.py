"""log_measurement: nine kinds, one live value per kind per day, the duplicate guard, replace and restore."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import db, rules, services, users
from app.models import MEASUREMENT_KINDS, Measurement
from app.services.entries import _live_unique


@pytest.fixture
def user(make_user):
    return make_user()


def measure(user, kind, value, by="claude", **data):
    return services.log_measurement(user.id, {"kind": kind, "value": value, **data}, by)


def rows(user_id):
    with db.new_session() as s:
        return [(m.kind, m.date, m.value, m.deleted_at is not None) for m in s.scalars(
            select(Measurement).where(Measurement.user_id == user_id).order_by(Measurement.id))]


def age_all(user_id, minutes):
    with db.session_scope() as s:
        for m in s.scalars(select(Measurement).where(Measurement.user_id == user_id)):
            m.created_at = m.created_at - timedelta(minutes=minutes)


def test_a_replacement_that_prints_the_same_shows_one_more_decimal(user):
    measure(user, "waist", 84.25)
    r = measure(user, "waist", 84.34)                       # both read 84.3 cm at 1 dp
    d = users.today_for(user)
    assert r["summary"] == f"Waist 84.34 cm on {d:%a} {d.day} {d:%b}. Replaced 84.25 cm."
    assert r["note"].startswith(f"Replaced the {d:%a} {d.day} {d:%b} waist of 84.25 cm: one a day.")
    again = measure(user, "waist", 86)
    assert again["summary"].endswith(". Replaced 84.3 cm.") and "of 84.3 cm" in again["note"]


def test_a_measurement_returns_the_contracts_shape(user):
    r = measure(user, "Waist", 84)
    assert set(r) == {"kind", "id", "duplicate", "measurement", "replaced", "recent", "summary", "links"}
    assert r["kind"] == "measurement" and r["duplicate"] is False and r["replaced"] is None
    m = r["measurement"]
    today = users.today_for(user).isoformat()
    assert m["id"] == r["id"] and m["kind"] == "waist" and m["value"] == 84 and m["unit"] == "cm" and m["date"] == today
    assert "user_id" not in m and "dedupe_key" not in m
    assert r["recent"] == [{"date": today, "value": 84}]
    d = users.today_for(user)   # the summary's date is the D12 form (D13); the link keeps ISO
    assert r["summary"] == f"Waist 84 cm on {d:%a} {d.day} {d:%b}." and r["links"]["day"].endswith(f"/day/{today}")


def test_the_table_is_the_contracts():
    assert set(rules.MEASUREMENTS) == set(MEASUREMENT_KINDS)
    assert rules.MEASUREMENTS["waist"] == ("cm", 30, 300, False) and rules.MEASUREMENTS["arm"] == ("cm", 10, 100, False)
    assert rules.MEASUREMENTS["thigh"] == ("cm", 20, 150, False)
    assert rules.MEASUREMENTS["steps"] == ("steps", 0, 200_000, True)
    assert rules.MEASUREMENTS["sleep_hours"] == ("h", 0, 24, False)
    assert rules.MEASUREMENTS["resting_hr"] == ("bpm", 20, 250, True)
    assert rules.MEASUREMENTS["water_ml"] == ("ml", 0, 20_000, True)


@pytest.mark.parametrize("kind,unit,lo,hi,whole", [(k, *v) for k, v in rules.MEASUREMENTS.items()])
def test_every_kind_keeps_its_unit_and_range(user, kind, unit, lo, hi, whole):
    for edge in (lo, hi):
        r = measure(user, kind, edge, allow_repeat=True)
        assert r["measurement"]["unit"] == unit and r["measurement"]["value"] == edge, r
    for outside in (lo - 1, hi + 1):
        assert measure(user, kind, outside)["error"] == "invalid_field"
    fraction = measure(user, kind, lo + 0.5)
    assert ("error" in fraction) is whole, fraction
    if whole:
        assert "whole number" in fraction["fix"]


def test_two_decimals_at_most(user):
    assert measure(user, "waist", 84.567)["measurement"]["value"] == 84.57
    assert measure(user, "sleep_hours", "7.25")["measurement"]["value"] == 7.25


def test_kinds_are_matched_like_exercise_names(user):
    assert measure(user, " Resting  HR ", 60)["measurement"]["kind"] == "resting_hr"
    assert measure(user, "sleep_hours", 7)["measurement"]["kind"] == "sleep_hours"
    bad = measure(user, "wrist", 20)
    assert bad["error"] == "invalid_kind" and "waist" in bad["closest"] and "water_ml" in bad["fix"]
    for odd in (None, 5, ["waist"], "x" * 10_000):
        r = measure(user, odd, 84)
        assert r["error"] == "invalid_kind" and len(str(r)) < 2_000
    assert rows(user.id) == [("resting_hr", users.today_for(user), 60, False), ("sleep_hours", users.today_for(user), 7, False)]


def test_bad_values_and_dates_come_back_together(user):
    r = measure(user, "waist", "lots", date="2999-01-01")
    assert {e["error"] for e in r["errors"]} == {"invalid_field", "date_in_future"}
    assert measure(user, "waist", None)["error"] == "missing_field"
    assert measure(user, "waist", 10 ** 400)["error"] == "invalid_field"
    old = (users.today_for(user) - timedelta(days=800)).isoformat()
    assert measure(user, "waist", 84, date=old)["error"] == "date_too_old"
    assert services.log_measurement(user.id, {"kind": "waist", "value": 84}, "robot")["error"] == "invalid_by"
    assert services.log_measurement(user.id, ["waist"], "web")["error"] == "invalid_field"
    assert services.log_measurement(987654, {"kind": "waist", "value": 84}, "web")["error"] == "no_account"
    assert rows(user.id) == []


def test_the_same_value_within_ten_minutes_is_a_duplicate(user):
    first = measure(user, "waist", 84)
    again = measure(user, "waist", 84, by="web")
    assert again["duplicate"] is True and again["id"] == first["id"] and "allow_repeat" in again["note"]
    assert again["summary"].startswith("Already logged:") and len(rows(user.id)) == 1
    assert measure(user, "hips", 84)["duplicate"] is False  # another kind is another entry


def test_a_different_value_the_same_day_replaces_and_can_be_restored(user):
    first = measure(user, "waist", 84)
    second = measure(user, "waist", 83.5)
    assert second["replaced"]["id"] == first["id"] and second["replaced"]["value"] == 84
    assert "Replaced 84 cm" in second["summary"] and "restore_entry" in second["note"]
    assert second["recent"] == [{"date": users.today_for(user).isoformat(), "value": 83.5}]
    back = services.restore_entry(user.id, "measurement", first["id"])
    assert [r["id"] for r in back["replaced"]] == [second["id"]] and back["entry"]["value"] == 84
    assert [(v, deleted) for _, _, v, deleted in rows(user.id)] == [(84, False), (83.5, True)]


def test_the_same_value_later_or_with_allow_repeat_replaces(user):
    first = measure(user, "waist", 84)
    age_all(user.id, 11)
    later = measure(user, "waist", 84)
    assert later["duplicate"] is False and later["replaced"]["id"] == first["id"]
    again = measure(user, "waist", 84, allow_repeat=True)
    assert again["replaced"]["id"] == later["id"]


def test_one_live_value_per_kind_per_day_is_the_new_index(user):
    assert ["kind", "date"] in _live_unique(Measurement)
    measure(user, "waist", 84)
    measure(user, "waist", 85)
    measure(user, "waist", 86, date=(users.today_for(user) - timedelta(days=1)).isoformat())
    with db.new_session() as s:
        live = s.execute(select(Measurement.date, func.count()).where(
            Measurement.user_id == user.id, Measurement.deleted_at.is_(None)).group_by(Measurement.date)).all()
    assert sorted(n for _, n in live) == [1, 1]


def test_recent_is_this_kind_newest_first_up_to_ten(user):
    today = users.today_for(user)
    for n in range(12):
        measure(user, "waist", 80 + n, date=(today - timedelta(days=n)).isoformat())
    measure(user, "hips", 95)
    r = measure(user, "waist", 70, date=(today - timedelta(days=20)).isoformat())
    assert len(r["recent"]) == 10 and r["recent"][0] == {"date": today.isoformat(), "value": 80}
    assert all(item["value"] != 95 for item in r["recent"])
    assert [item["date"] for item in r["recent"]] == sorted((item["date"] for item in r["recent"]), reverse=True)


def test_after_a_change_of_zone_the_one_logged_just_now_leads(user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc))
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "claude")
    assert measure(user, "waist", 84)["measurement"]["date"] == "2026-11-10"
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "claude")
    now = measure(user, "waist", 85)
    assert now["measurement"]["date"] == "2026-11-09" and now["replaced"] is None
    assert now["recent"] == [{"date": "2026-11-09", "value": 85}, {"date": "2026-11-10", "value": 84}]
    assert measure(user, "waist", 83, date="2026-11-10")["error"] == "date_in_future"
