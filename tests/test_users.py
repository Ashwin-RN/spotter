"""Identity: one row per person, kept up to date without losing anything, a daily signup cap, a safe
first-sign-in race, and "today" in the person's own time zone."""

import dataclasses
from datetime import date, datetime, timedelta, timezone
from zoneinfo import available_timezones

import pytest
from sqlalchemy import func, select

import app.users as users
from app import config, db
from app.models import User
from app.users import set_time_zone, today_for, now_for, user_exists, user_for_identity


def _count() -> int:
    with db.new_session() as session:
        return session.scalar(select(func.count()).select_from(User))


def _reload(user_id: int) -> User:
    with db.new_session() as session:
        return session.get(User, user_id)


def test_the_same_identity_twice_gives_one_row():
    first = user_for_identity("user_01A", "asha@example.com", "Asha", issuer="https://example.authkit.app")
    second = user_for_identity("user_01A", "asha@example.com", "Asha")
    assert isinstance(first, User) and isinstance(second, User)
    assert first.id == second.id and _count() == 1
    assert first.provider == "workos" and first.subject == "user_01A"
    assert first.claude_can == "log" and first.add_back_exercise_kcal is False and first.time_zone is None


def test_the_provider_comes_from_the_settings_not_the_issuer():
    a = user_for_identity("user_01A", None, None, issuer="https://one.example")
    b = user_for_identity("user_01A", None, None, issuer="https://two.example")
    assert a.id == b.id and _reload(a.id).issuer == "https://two.example"
    config.use_settings(dataclasses.replace(config.get_settings(), auth_provider="other"))
    c = user_for_identity("user_01A", None, None)
    assert c.id != a.id and c.provider == "other"


def test_a_second_sign_in_updates_a_changed_email_and_name():
    first = user_for_identity("user_01A", "old@example.com", "Asha")
    user_for_identity("user_01A", "new@example.com", "Asha Rao")
    stored = _reload(first.id)
    assert stored.email == "new@example.com" and stored.name == "Asha Rao"
    assert stored.updated_at >= first.updated_at


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_missing_value_never_erases_a_stored_one(missing):
    first = user_for_identity("user_01A", "asha@example.com", "Asha")
    user_for_identity("user_01A", missing, missing)
    stored = _reload(first.id)
    assert stored.email == "asha@example.com" and stored.name == "Asha"


@pytest.mark.parametrize("subject", [None, "", "  ", 42])
def test_an_empty_subject_is_refused(subject):
    result = user_for_identity(subject, "asha@example.com", "Asha")
    assert isinstance(result, dict) and result["error"] == "no_subject" and result["fix"]
    assert _count() == 0


def test_last_seen_is_written_at_most_once_a_minute(monkeypatch):
    start = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    user = user_for_identity("user_01A", None, None)
    clock["now"] = start + timedelta(seconds=30)
    user_for_identity("user_01A", None, None)
    assert _reload(user.id).last_seen_at == start
    clock["now"] = start + timedelta(seconds=61)
    user_for_identity("user_01A", None, None)
    stored = _reload(user.id)
    assert stored.last_seen_at == start + timedelta(seconds=61)
    assert stored.updated_at == start  # being seen is not an edit


def test_the_signup_cap_closes_new_accounts_for_the_day(monkeypatch):
    config.use_settings(dataclasses.replace(config.get_settings(), signups_per_day=2))
    assert isinstance(user_for_identity("user_01A", None, None), User)
    assert isinstance(user_for_identity("user_01B", None, None), User)
    refused = user_for_identity("user_01C", None, None)
    assert refused["error"] == "signups_closed" and refused["message"] and refused["fix"]
    assert _count() == 2
    assert isinstance(user_for_identity("user_01A", None, None), User)  # existing people still sign in


def test_the_signup_cap_counts_only_today_in_utc(make_user, monkeypatch):
    config.use_settings(dataclasses.replace(config.get_settings(), signups_per_day=1))
    now = datetime(2026, 11, 9, 0, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(db, "utcnow", lambda: now)
    make_user(created_at=now - timedelta(minutes=10))  # yesterday, UTC
    assert isinstance(user_for_identity("user_01A", None, None), User)
    assert user_for_identity("user_01B", None, None)["error"] == "signups_closed"


def test_two_first_sign_ins_at_once_give_one_row(monkeypatch):
    """The second sign-in looks first, finds nothing, and only then does the first one's insert land."""
    real_find = users._find
    calls = {"n": 0}

    def find_then_lose_the_race(session, provider, subject):
        calls["n"] += 1
        if calls["n"] == 1:
            found = real_find(session, provider, subject)
            with db.session_scope() as other:  # the other sign-in, committing in between
                now = db.utcnow()
                other.add(User(provider=provider, subject=subject, email="first@example.com",
                               created_at=now, updated_at=now, last_seen_at=now))
            return found
        return real_find(session, provider, subject)

    monkeypatch.setattr(users, "_find", find_then_lose_the_race)
    result = user_for_identity("user_01A", None, "Asha")
    assert calls["n"] == 2  # the insert hit the unique key, and the row was read again
    assert isinstance(result, User) and _count() == 1
    stored = _reload(result.id)
    assert stored.email == "first@example.com" and stored.name == "Asha"


def test_user_exists(make_user):
    user = make_user()
    assert user_exists(user.id) and not user_exists(user.id + 1000)


# ---------------------------------------------------------------- time zones

def test_zone_data_is_available():
    assert {"UTC", "Asia/Kolkata", "Pacific/Kiritimati", "America/Los_Angeles"} <= available_timezones()


def test_today_follows_the_time_zone_across_the_date_line(make_user):
    at = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
    assert today_for(make_user(time_zone="Pacific/Kiritimati"), at) == date(2026, 11, 10)  # UTC+14
    assert today_for(make_user(time_zone="Pacific/Pago_Pago"), at) == date(2026, 11, 9)  # UTC-11
    assert today_for(make_user(time_zone="Asia/Kolkata"), datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc)) \
        == date(2026, 11, 10)
    assert now_for(make_user(time_zone="Asia/Kolkata"), at).utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize("zone", [None, "", "Mars/Olympus", "../../etc/passwd"])
def test_today_is_utc_when_the_zone_is_unset_or_not_real(make_user, zone):
    at = datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc)
    user = make_user(time_zone=zone)
    assert today_for(user, at) == date(2026, 11, 9)
    assert now_for(user, at).utcoffset() == timedelta(0)


def test_today_reads_the_one_clock(make_user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc))
    assert today_for(make_user(time_zone="Pacific/Kiritimati")) == date(2026, 11, 10)


def test_a_time_zone_is_stored(make_user):
    user = make_user()
    result = set_time_zone(user.id, " Asia/Kolkata ")
    assert isinstance(result, User) and result.time_zone == "Asia/Kolkata"
    assert _reload(user.id).time_zone == "Asia/Kolkata"


@pytest.mark.parametrize("name", ["Mars/Olympus", "", "asia/kolkata", "../../etc/passwd", None, 5])
def test_a_bad_time_zone_is_refused(make_user, name):
    user = make_user(time_zone="Europe/London")
    result = set_time_zone(user.id, name)
    assert result["error"] == "unknown_time_zone" and result["fix"]
    assert _reload(user.id).time_zone == "Europe/London"


def test_a_time_zone_for_an_unknown_user_is_not_found():
    assert set_time_zone(999, "Asia/Kolkata")["error"] == "not_found"


def test_a_time_without_a_zone_is_refused(make_user):
    user = make_user(time_zone="Asia/Kolkata")
    with pytest.raises(ValueError):
        today_for(user, datetime(2026, 11, 9, 20, 0))
    with pytest.raises(ValueError):
        now_for(user, datetime(2026, 11, 9, 20, 0))


def test_an_over_long_subject_is_refused_before_the_database():
    assert isinstance(user_for_identity("u" * 255, None, None), User)
    result = user_for_identity("u" * 256, None, None)
    assert result["error"] == "no_subject" and "255" in result["message"] and result["fix"]
    assert _count() == 1


def test_an_over_long_email_or_name_is_cut_to_fit_not_refused():
    long_email, long_name = "a" * 400 + "@example.com", "N" * 300
    user = user_for_identity("user_01A", long_email, long_name)
    assert isinstance(user, User)
    stored = _reload(user.id)
    assert stored.email == long_email[:320] and stored.name == long_name[:128]
