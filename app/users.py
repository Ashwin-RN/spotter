"""Who a signed-in person is, and what day it is for them.

Both sign-in paths end here: a verified MCP token and the web sign-in callback each call
user_for_identity(), which finds or creates the one user row for that person. A user is keyed on
(provider, subject). The provider comes from the settings, never from a token, so a different
issuer can never split one person into two accounts.

"Today" for a person is the date in their own time zone. Until they set one, it is UTC.
"""

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
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

    The daily signup cap is a soft limit: it counts today's accounts, then inserts, without a lock.
    Simultaneous first sign-ins can each see room under the cap, so it can be passed by at most the
    number of first sign-ins in flight at that moment. That is acceptable for a rate limit.
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
        if _signups_today(session, now) >= settings.signups_per_day:
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
    return frozenset(available_timezones())


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


def set_time_zone(user_id: int, name: str) -> User | dict:
    """Store the person's IANA time zone, such as Asia/Kolkata. Returns the user row or an error."""
    cleaned = name.strip() if isinstance(name, str) else ""
    if cleaned not in _zone_names():
        return error("unknown_time_zone", f"{name!r} is not a time zone name Spotter knows."[:200],
                     "Use an IANA name such as Asia/Kolkata, Europe/London or America/New_York.")
    with db.new_session() as session:
        user = session.get(User, user_id)
        if user is None:
            return error("not_found", f"No user with id {user_id}.", "Sign in again.")
        if user.time_zone != cleaned:
            user.time_zone = cleaned
            user.updated_at = db.utcnow()
            session.commit()
        return user
