"""log_bodyweight: one live weigh-in a day, the duplicate guard, replace and restore, and the trend."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select

from app import db, services, users
from app.models import Bodyweight


@pytest.fixture
def user(make_user):
    return make_user()


def weigh(user, kg, days_ago=None, by="claude", **data):
    if days_ago is not None:
        data["date"] = (users.today_for(user) - timedelta(days=days_ago)).isoformat()
    return services.log_bodyweight(user.id, {"kg": kg, **data}, by)


def rows(user_id):
    """(date, kg, deleted) for every weigh-in of this person, oldest id first."""
    with db.new_session() as s:
        return [(b.date, b.kg, b.deleted_at is not None) for b in s.scalars(
            select(Bodyweight).where(Bodyweight.user_id == user_id).order_by(Bodyweight.id))]


def live_per_day(user_id):
    with db.new_session() as s:
        return dict(s.execute(select(Bodyweight.date, func.count()).where(
            Bodyweight.user_id == user_id, Bodyweight.deleted_at.is_(None)).group_by(Bodyweight.date)).all())


def age_all(user_id, minutes):
    with db.session_scope() as s:
        for row in s.scalars(select(Bodyweight).where(Bodyweight.user_id == user_id)):
            row.created_at = row.created_at - timedelta(minutes=minutes)


def test_trend_recent_and_a_different_weight_the_same_day_replaces(user):
    today = users.today_for(user).isoformat()
    first = weigh(user, 80)
    assert first["kind"] == "bodyweight" and first["duplicate"] is False and first["replaced"] is None
    assert first["weigh_in"]["date"] == today and first["weigh_in"]["kg"] == 80
    assert first["trend"]["method"] == "single weigh-in" and first["links"]["progress"].endswith("/progress")
    for days_ago, kg in ((30, 84), (29, 83.6), (3, 81)):
        weigh(user, kg, days_ago)
    r = weigh(user, 79.5, by="web")  # today again, a different weight: replaces 80
    assert r["replaced"]["id"] == first["id"] and r["replaced"]["kg"] == 80 and r["replaced"]["deleted_at"]
    assert "Replaced" in r["note"] and str(first["id"]) in r["note"] and "kept, not erased" in r["note"]
    assert r["weigh_in"]["logged_by"] == "web"
    assert [w["kg"] for w in r["recent"]] == [79.5, 81, 83.6, 84]
    assert r["trend"]["method"] == "7-day average"
    assert r["trend"]["now_avg"] == 80.25 and r["trend"]["then_avg"] == 83.8 and r["trend"]["change_kg"] == -3.55
    assert live_per_day(user.id)[users.today_for(user)] == 1


def test_failures_come_back_together(user):
    bad = services.log_bodyweight(user.id, {"kg": 401, "date": "2999-01-01"}, "claude")
    assert {e["error"] for e in bad["errors"]} == {"date_in_future", "invalid_field"}
    assert "weigh-ins" in bad["fix"]
    assert weigh(user, float("nan"))["error"] == "invalid_field"
    assert weigh(user, 19.9)["error"] == "invalid_field"
    assert weigh(user, True)["error"] == "invalid_field"
    assert services.log_bodyweight(user.id, {}, "claude")["error"] == "missing_field"
    assert services.log_bodyweight(user.id, None, "claude")["error"] == "missing_field"
    assert weigh(user, 70, date="yesterday")["error"] == "invalid_date"
    assert services.log_bodyweight(user.id, {"kg": 70}, "nobody")["error"] == "invalid_by"
    assert rows(user.id) == []


def test_huge_numbers_and_datetimes(user):
    assert weigh(user, 10 ** 400)["error"] == "invalid_field"
    assert len(str(weigh(user, "9" * 100_000))) < 2_000
    when = datetime.combine(users.today_for(user) - timedelta(days=1), datetime.min.time())
    assert services.log_bodyweight(user.id, {"kg": 80, "date": when}, "web")["weigh_in"]["date"] == \
        when.date().isoformat()


def test_a_weigh_in_dated_after_today_is_left_out(user):
    """A row can sit after today when the person moves their time zone back. Reads ignore it."""
    with db.session_scope() as s:
        s.add(Bodyweight(user_id=user.id, date=users.today_for(user) + timedelta(days=1), kg=60, logged_by="web",
                         dedupe_key="f" * 64))
    r = weigh(user, 80)
    assert r["recent"] == [{"date": users.today_for(user).isoformat(), "kg": 80}]
    assert r["trend"]["weigh_ins"] == 1


def test_dates_more_than_two_years_back_are_refused(user):
    assert weigh(user, 70, days_ago=800)["error"] == "date_too_old"
    assert "error" not in weigh(user, 70, days_ago=700)


def test_the_same_weight_within_ten_minutes_is_a_duplicate(user):
    first = weigh(user, 80)
    again = weigh(user, 80, by="web")
    assert again["duplicate"] is True and again["id"] == first["id"] and again["replaced"] is None
    assert again["weigh_in"] == first["weigh_in"] and "allow_repeat" in again["note"]
    assert len(rows(user.id)) == 1


def test_the_same_weight_later_in_the_day_replaces_the_earlier_one(user):
    first = weigh(user, 80)
    age_all(user.id, 11)
    later = weigh(user, 80)
    assert later["duplicate"] is False and later["id"] != first["id"] and later["replaced"]["id"] == first["id"]
    assert [deleted for _, _, deleted in rows(user.id)] == [True, False]


def test_allow_repeat_replaces_instead_of_returning_the_first(user):
    first = weigh(user, 80)
    repeat = weigh(user, 80, allow_repeat=True)
    assert repeat["duplicate"] is False and repeat["replaced"]["id"] == first["id"]
    assert live_per_day(user.id) == {users.today_for(user): 1}


def test_other_dates_are_untouched(user):
    weigh(user, 82, days_ago=1)
    weigh(user, 81)
    weigh(user, 80.5)  # replaces today's only
    assert [(kg, deleted) for _, kg, deleted in rows(user.id)] == [(82, False), (81, True), (80.5, False)]


def test_replace_then_restore_swaps_and_swaps_back(user):
    first = weigh(user, 80)
    second = weigh(user, 79.5)
    assert second["replaced"]["id"] == first["id"]

    back = services.restore_entry(user.id, "bodyweight", first["id"])
    assert back["restored"] is True and back["entry"]["kg"] == 80 and back["entry"]["deleted_at"] is None
    assert [r["id"] for r in back["replaced"]] == [second["id"]] and "swaps them back" in back["note"]
    assert live_per_day(user.id) == {users.today_for(user): 1}
    assert services.get_entry(user.id, "bodyweight", second["id"])["error"] == "not_found"

    again = services.restore_entry(user.id, "bodyweight", second["id"])
    assert [r["id"] for r in again["replaced"]] == [first["id"]] and again["entry"]["kg"] == 79.5
    assert [(kg, deleted) for _, kg, deleted in rows(user.id)] == [(80, True), (79.5, False)]
    # The trend reads only the live weigh-in.
    assert weigh(user, 79.5)["recent"] == [{"date": users.today_for(user).isoformat(), "kg": 79.5}]


def test_restoring_a_weigh_in_on_a_day_with_no_live_one_replaces_nothing(user):
    first = weigh(user, 80)
    services.delete_entry(user.id, "bodyweight", first["id"])
    assert live_per_day(user.id) == {}
    back = services.restore_entry(user.id, "bodyweight", first["id"])
    assert back["replaced"] == [] and "note" not in back
    assert live_per_day(user.id) == {users.today_for(user): 1}


def test_the_default_date_is_the_persons_today(make_user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc))
    ahead, plain = make_user(time_zone="Pacific/Kiritimati"), make_user()
    assert weigh(ahead, 80)["weigh_in"]["date"] == "2026-11-10"
    assert weigh(plain, 80)["weigh_in"]["date"] == "2026-11-09"
    assert services.log_bodyweight(plain.id, {"kg": 80, "date": "2026-11-10"}, "web")["error"] == "date_in_future"


def test_log_bodyweight_query_count(user):
    weigh(user, 70, days_ago=2)
    weigh(user, 70.2, days_ago=1)
    count = [0]

    def counter(*_):
        count[0] += 1

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        r = weigh(user, 70.4, days_ago=1)  # a replace: the most work a weigh-in does
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    assert "error" not in r and r["replaced"] and count[0] <= 10, count[0]
