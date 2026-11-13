"""Spotter's services: everything the web pages and the MCP functions do, in one place for both.

One module per area, so each later stage adds its own:

  account       whoami, update_settings, set_claude_can (the Settings page only), list_exercises,
                delete_account (the Settings page only), account_empty (an empty account's Today)
  lifts         log_lifts
  body          log_bodyweight
  measurements  log_measurement
  food          log_food, save_food, find_food (usda: the cached FoodData Central lookup),
                list_saved_foods (the Foods page)
  cardio        log_cardio, get_cardio (the web only: an activity's sessions and bests)
  goals         set_goal, get_goals, goal_charts (the Goals page only)
  trends        get_trends, get_days (the web only: the 28-day grid)
  progress      get_progress
  log           get_log
  entries       get_entry, edit_entry, delete_entry, restore_entry
  export        export_data (the Settings page's download)
  imports       import_entries (history from another app, many dated rows a call)
  owner         owner_calls (the call log, for Spotter's owner alone: it reads every account, so it takes no
                user_id)
  common        the by-id lookup, the user-row lock, the write limit, the read throttle, the duplicate guard

Every public function takes the signed-in person's user_id (the account row id) first, and writes
take `by` ("web" or "claude"), stored as logged_by (an import stores "import"). Each returns a plain JSON-ready dict: the result,
or {error, message, fix}, where the fix says what to send instead. A set_goal refusal about one input
also names it in field. Nothing here takes a request or a
site address: links come from app.links. Dates come from users.today_for, times from db.utcnow.
Every write locks the user row first, then checks the write limit (common.WRITE_FUNCTIONS).
"""

from app.services.account import (
    account_empty, delete_account, list_exercises, set_claude_can, update_settings, whoami,
)
from app.services.body import log_bodyweight
from app.services.common import (
    DUPLICATE_MINUTES, READ_LIMIT, WRITE_FUNCTIONS, WRITE_LIMIT, WRITE_WINDOW_MINUTES,
)
from app.services.entries import delete_entry, edit_entry, entry_kinds, get_entry, restore_entry
from app.services.cardio import get_cardio, log_cardio
from app.services.export import export_data
from app.services.food import find_food, list_saved_foods, log_food, save_food
from app.services.goals import get_goals, goal_charts, set_goal
from app.services.imports import import_entries
from app.services.lifts import log_lifts
from app.services.log import get_log
from app.services.measurements import log_measurement
from app.services.owner import owner_calls
from app.services.progress import get_progress
from app.services.trends import get_days, get_trends

__all__ = [
    "DUPLICATE_MINUTES", "READ_LIMIT", "WRITE_FUNCTIONS", "WRITE_LIMIT", "WRITE_WINDOW_MINUTES",
    "account_empty", "delete_account", "delete_entry", "edit_entry", "entry_kinds", "export_data", "find_food",
    "get_cardio", "get_days", "get_entry", "get_goals", "get_log", "goal_charts", "get_progress", "get_trends",
    "import_entries", "list_exercises", "list_saved_foods",
    "log_bodyweight", "log_cardio", "log_food", "log_lifts", "log_measurement", "owner_calls",
    "restore_entry", "save_food", "set_claude_can", "set_goal", "update_settings", "whoami",
]
