"""Spotter's services: everything the web pages and the MCP functions do, in one place for both.

One module per area, so each later stage adds its own:

  account   whoami, update_settings, list_exercises
  lifts     log_lifts
  body      log_bodyweight
  goals     set_goal
  progress  get_progress
  entries   get_entry, delete_entry, restore_entry
  common    the by-id lookup, the user-row lock, the duplicate guard

Every public function takes the signed-in person's user_id (the account row id) first, and writes
take `by` ("web" or "claude"), stored as logged_by. Each returns a plain JSON-ready dict: the result,
or {error, message, fix}, where the fix says what to send instead. Nothing here takes a request or a
site address: links come from app.links. Dates come from users.today_for, times from db.utcnow.
"""

from app.services.account import list_exercises, update_settings, whoami
from app.services.body import log_bodyweight
from app.services.common import DUPLICATE_MINUTES
from app.services.entries import delete_entry, entry_kinds, get_entry, restore_entry
from app.services.goals import set_goal
from app.services.lifts import log_lifts
from app.services.progress import get_progress

__all__ = [
    "DUPLICATE_MINUTES", "delete_entry", "entry_kinds", "get_entry", "get_progress", "list_exercises",
    "log_bodyweight", "log_lifts", "restore_entry", "set_goal", "update_settings", "whoami",
]
