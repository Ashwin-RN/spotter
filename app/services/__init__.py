"""Spotter's services: everything the web pages and the MCP functions do, in one place for both.

One module per area, so each later stage adds its own:

  account       whoami, update_settings, set_claude_can (the Settings page only), list_exercises
  lifts         log_lifts
  body          log_bodyweight
  measurements  log_measurement
  food          log_food, save_food, find_food (usda: the cached FoodData Central lookup)
  cardio        log_cardio
  goals         set_goal
  progress      get_progress
  log           get_log
  entries       get_entry, edit_entry, delete_entry, restore_entry
  common        the by-id lookup, the user-row lock, the write limit, the duplicate guard

Every public function takes the signed-in person's user_id (the account row id) first, and writes
take `by` ("web" or "claude"), stored as logged_by. Each returns a plain JSON-ready dict: the result,
or {error, message, fix}, where the fix says what to send instead. Nothing here takes a request or a
site address: links come from app.links. Dates come from users.today_for, times from db.utcnow.
Every write locks the user row first, then checks the write limit (common.WRITE_FUNCTIONS).
"""

from app.services.account import list_exercises, set_claude_can, update_settings, whoami
from app.services.body import log_bodyweight
from app.services.common import DUPLICATE_MINUTES, WRITE_FUNCTIONS, WRITE_LIMIT, WRITE_WINDOW_MINUTES
from app.services.entries import delete_entry, edit_entry, entry_kinds, get_entry, restore_entry
from app.services.cardio import log_cardio
from app.services.food import find_food, log_food, save_food
from app.services.goals import set_goal
from app.services.lifts import log_lifts
from app.services.log import get_log
from app.services.measurements import log_measurement
from app.services.progress import get_progress

__all__ = [
    "DUPLICATE_MINUTES", "WRITE_FUNCTIONS", "WRITE_LIMIT", "WRITE_WINDOW_MINUTES",
    "delete_entry", "edit_entry", "entry_kinds", "find_food", "get_entry", "get_log", "get_progress",
    "list_exercises", "log_bodyweight", "log_cardio", "log_food", "log_lifts", "log_measurement", "restore_entry",
    "save_food", "set_claude_can", "set_goal", "update_settings", "whoami",
]
