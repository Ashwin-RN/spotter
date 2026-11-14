"""Who a signed-in person is, and what day it is for them.

Both sign-in paths end here: a verified MCP token and the web sign-in callback each call
user_for_identity(), which finds or creates the one user row for that person. A user is keyed on
(provider, subject). The provider comes from the settings, never from a token, so a different
issuer can never split one person into two accounts.

"Today" for a person is the date in their own time zone. Until they set one, it is UTC.
"""

import importlib.resources
import zoneinfo
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db
from app.config import get_settings
from app.models import User

LAST_SEEN_EVERY = timedelta(minutes=1)
SUBJECT_MAX, EMAIL_MAX, NAME_MAX, ISSUER_MAX = 255, 320, 128, 255  # the column lengths


def error(code: str, message: str, fix: str) -> dict:
    return {"error": code, "message": message, "fix": fix}


def _clean(value: object, limit: int, cut: bool) -> str | None:
    """A usable new value, or None. Too long: cut to fit (email, name) or dropped (issuer)."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if len(value) > limit:
        return value[:limit] if cut else None
    return value


def _find(session: Session, provider: str, subject: str) -> User | None:
    return session.scalar(select(User).where(User.provider == provider, User.subject == subject))


def _signups_today(session: Session, now: datetime) -> int:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return session.scalar(select(func.count()).select_from(User).where(User.created_at >= midnight)) or 0


def _refresh(session: Session, user: User, email: str | None, name: str | None, issuer: str | None) -> None:
    """Update what changed. A missing value never erases one the other sign-in path supplied."""
    now = db.utcnow()
    edited = False
    for field, value in (("email", email), ("name", name), ("issuer", issuer)):
        if value is not None and value != getattr(user, field):
            setattr(user, field, value)
            edited = True
    if edited:
        user.updated_at = now
    if edited or user.last_seen_at is None or now - user.last_seen_at >= LAST_SEEN_EVERY:
        user.last_seen_at = now
    if session.dirty:
        session.commit()


def user_for_identity(subject: str | None, email: str | None, name: str | None,
                      issuer: str | None = None) -> User | dict:
    """The user row for this person, created on their first sign-in, or {error, message, fix}.

    With no daily signup cap set (SIGNUPS_PER_DAY unset or blank), every first sign-in gets an account
    and today's accounts are not counted. A cap set is a soft limit: it counts today's accounts, then
    inserts, without a lock. Simultaneous first sign-ins can each see room under the cap, so it can be
    passed by at most the number of first sign-ins in flight at that moment. That is acceptable for a
    rate limit. A cap of 0 takes no new accounts.
    """
    if not isinstance(subject, str) or not subject.strip():
        return error("no_subject", "The sign-in did not say who this is.",
                     "Sign in again. If it keeps happening, the sign-in provider is not sending a user id.")
    subject = subject.strip()
    if len(subject) > SUBJECT_MAX:
        return error("no_subject", f"The sign-in's user id is longer than {SUBJECT_MAX} characters.",
                     "Sign in again. If it keeps happening, the sign-in provider is sending an id Spotter "
                     "cannot store.")
    email, name = _clean(email, EMAIL_MAX, cut=True), _clean(name, NAME_MAX, cut=True)
    issuer = _clean(issuer, ISSUER_MAX, cut=False)
    settings = get_settings()
    provider = settings.auth_provider

    with db.new_session() as session:
        user = _find(session, provider, subject)
        if user is not None:
            _refresh(session, user, email, name, issuer)
            return user

        now = db.utcnow()
        cap = settings.signups_per_day
        if cap is not None and _signups_today(session, now) >= cap:
            return error("signups_closed", "Spotter is not taking new accounts for the rest of today.",
                         "Try again after midnight UTC.")
        user = User(provider=provider, subject=subject, email=email, name=name, issuer=issuer,
                    created_at=now, updated_at=now, last_seen_at=now)
        session.add(user)
        try:
            session.commit()
            return user
        except IntegrityError:
            # Another first sign-in for the same person won the race. Use its row.
            session.rollback()
            user = _find(session, provider, subject)
            if user is None:
                raise
            _refresh(session, user, email, name, issuer)
            return user


def user_exists(user_id: int) -> bool:
    with db.new_session() as session:
        return session.get(User, user_id) is not None


# ---------------------------------------------------------------- time zones and "today"

@lru_cache(maxsize=1)
def _zone_names() -> frozenset[str]:
    """Every name the zone database holds, aliases and abbreviations included."""
    return frozenset(available_timezones())


def _tz_file(name: str) -> str | None:
    """A file from the tz database, found where zoneinfo finds its data: the system folders first,
    then the tzdata package. None when neither has it."""
    for folder in zoneinfo.TZPATH:
        candidate = Path(folder) / name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    try:
        return (importlib.resources.files("tzdata") / "zoneinfo" / name).read_text(encoding="utf-8")
    except (ModuleNotFoundError, OSError):
        return None


def _fold(name: str) -> str:
    """The form zone names are compared in: no surrounding spaces, spaces as underscores, any case."""
    return "_".join(name.split()).casefold()


# Old names the link data sends to another country's city, because that city has had the same clocks
# since 1970. Each goes to its own country's zone instead. Checked by hand against every old name in the
# data (tests/test_users.py holds the reviewed list, so a new old name fails a test until it is checked).
OLD_NAMES = {
    "Africa/Asmera": "Africa/Asmara",              # Eritrea, not Kenya
    "Africa/Timbuktu": "Africa/Bamako",            # Mali, not Ivory Coast
    "America/Coral_Harbour": "America/Atikokan",   # Canada, not Panama
    "America/Virgin": "America/St_Thomas",         # US Virgin Islands, not Puerto Rico
    "Antarctica/South_Pole": "Antarctica/McMurdo",  # Antarctica, not New Zealand
    "Atlantic/Jan_Mayen": "Arctic/Longyearbyen",   # Svalbard and Jan Mayen, not Germany
    "Pacific/Ponape": "Pacific/Pohnpei",           # Micronesia, not the Solomon Islands
    "Pacific/Truk": "Pacific/Chuuk",               # Micronesia, not Papua New Guinea
    "Pacific/Yap": "Pacific/Chuuk",                # Micronesia, not Papua New Guinea
}


@lru_cache(maxsize=1)
def _zone_tables() -> tuple[dict[str, str], dict[str, str], frozenset[str] | None]:
    """(accepted names by folded form, old name -> current name, current names or None).

    Accepted: Area/Location names, and UTC. Not accepted, though the zone database holds them:
    abbreviation zones (EST, CET), GMT, fixed offsets (Etc/GMT+5) and other single-word aliases.
    UTC is always accepted, even on a machine with no zone data.
    Current names are zone.tab's: every country's own zones (Europe/Oslo, Asia/Kolkata), plus UTC.
    An accepted name that is not current is an old alias: OLD_NAMES first, then the database's link
    to a current name (Asia/Calcutta -> Asia/Kolkata). A current name is never rewritten, even where
    the database links it to another city with the same clocks (Europe/Oslo stays Europe/Oslo).
    Without zone.tab or the link data, old names stand as given."""
    accepted = sorted({n for n in _zone_names()
                       if ("/" in n and not n.startswith(("Etc/", "posix/", "right/"))) or n in ("UTC", "Etc/UTC")}
                      | {"UTC"})
    by_folded = {_fold(n): n for n in accepted}
    table = _tz_file("zone.tab")
    current = None
    if table is not None:
        listed = {line.split("\t")[2] for line in table.splitlines()
                  if line and not line.startswith("#") and len(line.split("\t")) >= 3}
        current = frozenset((listed & set(accepted)) | {"UTC"})
    links: dict[str, str] = {}
    for line in (_tz_file("tzdata.zi") or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "L":
            links[parts[2]] = parts[1]
    aliases = {"Etc/UTC": "UTC"}
    for name in accepted:
        if current is None or name in current:
            continue
        target = OLD_NAMES.get(name) if OLD_NAMES.get(name) in current else links.get(name)
        for _ in range(5):  # links point at zones, but follow a short chain if one ever appears
            if target is None or target in current:
                break
            target = links.get(target)
        if target in current:
            aliases[name] = target
    return by_folded, aliases, current


def canonical_zone(name: object) -> str | None:
    """The one check of a time zone name: the stored form of `name`, or None when it is not a zone a
    person can live in. The form is forgiven (surrounding spaces, letter case, spaces for
    underscores) and an old alias becomes its current name; the meaning is not forgiven:
    abbreviations, GMT, offsets and bare places give None. UTC is valid."""
    if not isinstance(name, str) or len(name) > 200:
        return None
    by_folded, aliases, _ = _zone_tables()
    found = by_folded.get(_fold(name))
    return aliases.get(found, found) if found is not None else None


def current_zone_names() -> tuple[str, ...]:
    """The names to offer a person choosing a zone, sorted: zone.tab's current names and UTC. Without
    zone.tab, every accepted name in its stored form. Each one survives canonical_zone unchanged."""
    by_folded, aliases, current = _zone_tables()
    if current is not None:
        return tuple(sorted(current))
    return tuple(sorted({aliases.get(n, n) for n in by_folded.values()}))


def closest_zones(text: object) -> list[str]:
    """Zones whose last part is this bare place (Kolkata -> Asia/Kolkata), in their stored form."""
    if not isinstance(text, str) or not text.strip() or len(text) > 200:
        return []
    by_folded, _, _ = _zone_tables()
    wanted = _fold(text)
    return sorted({canonical_zone(n) for n in by_folded.values() if "/" in n and _fold(n.rsplit("/", 1)[1]) == wanted})


def zone_for(name: str | None) -> ZoneInfo | timezone:
    """The zone with this IANA name, or UTC when the name is unset or not a real zone."""
    if name and name in _zone_names():
        try:
            return ZoneInfo(name)
        except (ValueError, OSError):
            pass
    return timezone.utc


def now_for(user: User, at: datetime | None = None) -> datetime:
    """The time now (or at `at`, which must carry a time zone) on the person's own clock."""
    if at is None:
        at = db.utcnow()
    elif at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("A time without a time zone was given; use app.db.utcnow().")
    return at.astimezone(zone_for(user.time_zone))


def today_for(user: User, at: datetime | None = None) -> date:
    """The person's own date today. The one place a date is read from the clock."""
    return now_for(user, at).date()
