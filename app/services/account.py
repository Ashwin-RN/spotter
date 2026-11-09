"""The account and the shared exercise list.

whoami(user_id)
    The signed-in person's account: account_id (the row id), user_id (the sign-in provider's subject,
    the same value the token's sub and the Settings page show), email, name, time zone, today's date
    for them, their targets, what Claude may do, and links. Until a time zone is set, today is the UTC
    date and time_zone_note tells Claude to ask for it.

list_exercises(kind=None, category=None)
    The names entries must use, filtered by kind (lift, cardio) and category.
"""

from zoneinfo import ZoneInfo

from app import db, exercises as exercise_list, links, users
from app.models import EXERCISE_KINDS, User
from app.rules import error, quote
from app.services.common import exercises, no_account

TIME_ZONE_NOTE = ("No time zone is set, so today is the UTC date. Ask the person which time zone they are in, "
                  "then ask them to set it on the Settings page (links.settings).")


def whoami(user_id: int) -> dict:
    """Returns {account_id, user_id, email, name, time_zone, today, targets, claude_can, links[,
    time_zone_note]} or {error, message, fix}."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        out = {
            "account_id": user.id,
            "user_id": user.subject,
            "email": user.email,
            "name": user.name,
            "time_zone": user.time_zone,
            "today": users.today_for(user).isoformat(),
            "targets": {
                "calorie_target_kcal": user.calorie_target_kcal,
                "target_sessions_per_week": user.target_sessions_per_week,
                "target_cardio_minutes_per_week": user.target_cardio_minutes_per_week,
            },
            "claude_can": user.claude_can,
            "links": {"settings": links.page("settings"), "home": links.page("home")},
        }
        if not isinstance(users.zone_for(user.time_zone), ZoneInfo):
            out["time_zone_note"] = TIME_ZONE_NOTE
        return out


def _categories() -> list[str]:
    return list(dict.fromkeys(exercise_list.CATEGORIES + [row["category"] for row in exercise_list.rows()]))


def list_exercises(kind: str | None = None, category: str | None = None) -> dict:
    """Returns {exercises [{name, kind, category}]} or {error, message, fix}."""
    if kind not in (None, "") and not (isinstance(kind, str) and kind in EXERCISE_KINDS):
        return error("invalid_field", f"kind '{quote(kind)}' is not one of {', '.join(EXERCISE_KINDS)}.",
                     f"Use one of: {', '.join(EXERCISE_KINDS)}, or leave it out for all.")
    categories = _categories()
    if category not in (None, "") and not (isinstance(category, str) and category in categories):
        return error("invalid_field", f"category '{quote(category)}' is not one of {', '.join(categories)}.",
                     f"Use one of: {', '.join(categories)}, or leave it out for all.")
    with db.new_session() as s:
        rows = [{"name": e.name, "kind": e.kind, "category": e.category} for e in exercises(s, kind or None)
                if not category or e.category == category]
    return {"exercises": rows}
