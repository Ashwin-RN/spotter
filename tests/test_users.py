"""Identity: one row per person, kept up to date without losing anything, a daily signup cap, a safe
first-sign-in race, and "today" in the person's own time zone."""

import dataclasses
from datetime import date, datetime, timedelta, timezone
from zoneinfo import available_timezones

import pytest
from sqlalchemy import func, select

import app.users as users
from app import config, db, services
from app.models import User
from app import users
from app.users import (canonical_zone, closest_zones, current_zone_names,
                       today_for, now_for, user_exists, user_for_identity)


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


# ---------------------------------------------------------------- canonical_zone: the one check

@pytest.mark.parametrize("given, stored", [
    ("Asia/Kolkata", "Asia/Kolkata"), (" asia/kolkata ", "Asia/Kolkata"), ("ASIA/KOLKATA", "Asia/Kolkata"),
    ("America/New York", "America/New_York"), ("america/new  york", "America/New_York"),
    ("America/Argentina/Buenos_Aires", "America/Argentina/Buenos_Aires"),
    ("UTC", "UTC"), ("utc", "UTC"), ("Etc/UTC", "UTC"),
    ("Asia/Calcutta", "Asia/Kolkata"), ("Europe/Kiev", "Europe/Kyiv"), ("US/Eastern", "America/New_York"),
    ("Europe/Oslo", "Europe/Oslo"), ("Europe/Stockholm", "Europe/Stockholm"),
])
def test_canonical_zone_forgives_the_form_and_updates_old_names(given, stored):
    assert canonical_zone(given) == stored


@pytest.mark.parametrize("given", [
    "CET", "EST", "PST", "BST", "GMT", "+05:30", "UTC+5:30", "GMT+5", "Etc/GMT+5", "Etc/GMT-14",
    "Bangalore", "France", "Japan", "Kolkata", "Asia/Bangalore", "", "   ", "Mars/Olympus",
    "../../etc/passwd", "posix/Asia/Kolkata", "Z" * 100_000, None, 5, 5.5, ["Asia/Kolkata"], b"Asia/Kolkata",
], ids=lambda value: repr(value)[:30])
def test_canonical_zone_refuses_what_no_one_lives_in(given):
    assert canonical_zone(given) is None


def test_every_offered_name_survives_canonical_zone_unchanged():
    names = current_zone_names()
    assert len(names) > 400 and {"Asia/Kolkata", "Europe/Oslo", "UTC"} <= set(names)
    assert all(canonical_zone(name) == name for name in names)


def test_closest_zones_names_a_bare_city():
    assert closest_zones("Kolkata") == ["Asia/Kolkata"] and closest_zones("new york") == ["America/New_York"]
    assert closest_zones("Calcutta") == ["Asia/Kolkata"] and closest_zones("Bangalore") == []
    assert closest_zones(None) == [] and closest_zones("  ") == []


@pytest.mark.parametrize("missing", ["zone.tab", "tzdata.zi"])
def test_without_the_alias_data_an_old_name_is_stored_as_given(monkeypatch, make_user, missing):
    real = users._tz_file
    monkeypatch.setattr(users, "_tz_file", lambda name: None if name == missing else real(name))
    users._zone_tables.cache_clear()
    try:
        assert canonical_zone("asia/calcutta") == "Asia/Calcutta"
        assert canonical_zone("Asia/Kolkata") == "Asia/Kolkata" and canonical_zone("EST") is None
        user = make_user()
        assert services.update_settings(user.id, {"time_zone": "Asia/Calcutta"}, "web")["time_zone"] == "Asia/Calcutta"
        assert _reload(user.id).time_zone == "Asia/Calcutta"
    finally:
        users._zone_tables.cache_clear()


# ---------------------------------------------------------------- old names and their own countries

def _countries() -> dict[str, str]:
    """zone.tab: zone name -> country code."""
    table = users._tz_file("zone.tab")
    return {line.split("\t")[2]: line.split("\t")[0] for line in table.splitlines()
            if line and not line.startswith("#")}


OWN_COUNTRY = {
    "Africa/Asmera": "Africa/Asmara", "Africa/Timbuktu": "Africa/Bamako", "America/Coral_Harbour": "America/Atikokan",
    "America/Virgin": "America/St_Thomas", "Antarctica/South_Pole": "Antarctica/McMurdo",
    "Atlantic/Jan_Mayen": "Arctic/Longyearbyen", "Pacific/Ponape": "Pacific/Pohnpei", "Pacific/Truk": "Pacific/Chuuk",
    "Pacific/Yap": "Pacific/Chuuk",
}


@pytest.mark.parametrize("old, own", sorted(OWN_COUNTRY.items()))
def test_an_old_name_goes_to_its_own_countrys_zone(old, own):
    assert canonical_zone(old) == own


def test_the_override_table_is_exactly_the_reviewed_one():
    assert users.OLD_NAMES == OWN_COUNTRY


def test_every_old_name_override_fixes_a_real_country_mistake():
    countries = _countries()
    links = {parts[2]: parts[1] for parts in (line.split() for line in users._tz_file("tzdata.zi").splitlines())
             if len(parts) >= 3 and parts[0] == "L"}
    for old, own in users.OLD_NAMES.items():
        assert old in users._zone_names() and old not in countries, old  # an old name the data still holds
        assert own in countries, own  # a current zone of its own country
        assert countries.get(links.get(old)) != countries[own], old  # the link alone would cross a border


# Every old Area/Location name in the zone data, each checked by hand for where it resolves. Nothing in the
# data says which country an old name belonged to, so the check cannot be computed; this list makes a new
# old name (from a tz data update) fail here until someone checks it and, if it crosses a border, adds it
# to users.OLD_NAMES.
REVIEWED_OLD_NAMES = frozenset("""
Africa/Asmera Africa/Timbuktu America/Argentina/ComodRivadavia America/Atka America/Buenos_Aires
America/Catamarca America/Coral_Harbour America/Cordoba America/Ensenada America/Fort_Wayne America/Godthab
America/Indianapolis America/Jujuy America/Knox_IN America/Louisville America/Mendoza America/Montreal
America/Nipigon America/Pangnirtung America/Porto_Acre America/Rainy_River America/Rosario
America/Santa_Isabel America/Shiprock America/Thunder_Bay America/Virgin America/Yellowknife
Antarctica/South_Pole Asia/Ashkhabad Asia/Calcutta Asia/Choibalsan Asia/Chongqing Asia/Chungking Asia/Dacca
Asia/Harbin Asia/Istanbul Asia/Kashgar Asia/Katmandu Asia/Macao Asia/Rangoon Asia/Saigon Asia/Tel_Aviv
Asia/Thimbu Asia/Ujung_Pandang Asia/Ulan_Bator Atlantic/Faeroe Atlantic/Jan_Mayen Australia/ACT
Australia/Canberra Australia/Currie Australia/LHI Australia/NSW Australia/North Australia/Queensland
Australia/South Australia/Tasmania Australia/Victoria Australia/West Australia/Yancowinna Brazil/Acre
Brazil/DeNoronha Brazil/East Brazil/West Canada/Atlantic Canada/Central Canada/Eastern Canada/Mountain
Canada/Newfoundland Canada/Pacific Canada/Saskatchewan Canada/Yukon Chile/Continental Chile/EasterIsland
Europe/Belfast Europe/Kiev Europe/Nicosia Europe/Tiraspol Europe/Uzhgorod Europe/Zaporozhye
Mexico/BajaNorte Mexico/BajaSur Mexico/General Pacific/Enderbury Pacific/Johnston Pacific/Ponape
Pacific/Samoa Pacific/Truk Pacific/Yap US/Alaska US/Aleutian US/Arizona US/Central US/East-Indiana
US/Eastern US/Hawaii US/Indiana-Starke US/Michigan US/Mountain US/Pacific US/Samoa
""".split())


def test_every_old_name_in_the_data_has_been_checked_for_its_country():
    _, aliases, current = users._zone_tables()
    assert current is not None  # the real data is present on this machine
    old = {name for name in aliases if "/" in name and name != "Etc/UTC"}
    assert old - REVIEWED_OLD_NAMES == set(), "new old names: check each one's country, then list it here"
    assert set(users.OLD_NAMES) <= REVIEWED_OLD_NAMES


def test_the_offered_list_holds_current_names_only():
    names = current_zone_names()
    assert all(canonical_zone(name) == name for name in names)
    assert not {"US/Eastern", "Asia/Calcutta", "Europe/Kiev", "Africa/Asmera", "Etc/UTC"} & set(names)
    assert len(names) < 450  # zone.tab's zones and UTC, not every name the data holds (over 500)


# ---------------------------------------------------------------- UTC always works

def test_with_no_zone_data_at_all_utc_is_still_accepted_and_offered(monkeypatch):
    monkeypatch.setattr(users, "_zone_names", lambda: frozenset())
    monkeypatch.setattr(users, "_tz_file", lambda name: None)
    users._zone_tables.cache_clear()
    try:
        assert canonical_zone("UTC") == "UTC" and canonical_zone(" utc ") == "UTC"
        assert canonical_zone("Asia/Kolkata") is None
        assert current_zone_names() == ("UTC",)
    finally:
        users._zone_tables.cache_clear()


def test_with_no_zone_table_utc_is_still_offered(monkeypatch):
    real = users._tz_file
    monkeypatch.setattr(users, "_tz_file", lambda name: None if name == "zone.tab" else real(name))
    users._zone_tables.cache_clear()
    try:
        assert "UTC" in current_zone_names() and canonical_zone("UTC") == "UTC"
    finally:
        users._zone_tables.cache_clear()
