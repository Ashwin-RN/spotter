"""Spotter's services: everything the web pages and the MCP functions do, in one place for both.

One module per area, so each later stage adds its own:

  account       whoami, update_settings, set_claude_can (the Settings page only), list_exercises, all_exercises
                (the exercises resource and the web's forms: every row, whatever the person logged),
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
  feedback      send_feedback (a note to Spotter's maker), owner_feedback (the notes, for Spotter's owner alone:
                no user_id either, and it only reads), owner_delete_feedback (the owner's delete of notes by id,
                no user_id either)
  routines      save_routine, get_routines (a workout saved by name, each lift with the weight to log, and the
                program when one is saved)
  programs      save_program (the person's training program: a weekday schedule in blocks of weeks, one a person)
  data          what_is_logged, get_data (data on request: any question about the person's own entries, from the
                field list in fields)
  common        the by-id lookup, the user-row lock, the write limit, the read throttle, the duplicate guard

Every public function takes the signed-in person's user_id (the account row id) first, and writes
take `by` ("web" or "claude"), stored as logged_by (an import stores "import"), but send_feedback, which only
MCP calls. Each returns a plain JSON-ready dict: the result,
or {error, message, fix}, where the fix says what to send instead. A set_goal refusal about one input
also names it in field. Nothing here takes a request or a
site address: links come from app.links. Dates come from users.today_for, times from db.utcnow.
Every write locks the user row first, then checks the write limit (common.WRITE_FUNCTIONS).
"""

from app.services.account import (
    account_empty, all_exercises, delete_account, list_exercises, set_claude_can, update_settings, whoami,
)
from app.services.body import log_bodyweight
from app.services.common import (
    DUPLICATE_MINUTES, READ_LIMIT, WRITE_FUNCTIONS, WRITE_LIMIT, WRITE_WINDOW_MINUTES,
)
from app.services.entries import delete_entry, edit_entry, entry_kinds, get_entry, restore_entry
from app.services.cardio import get_cardio, log_cardio
from app.services.data import get_data, what_is_logged
from app.services.export import export_data
from app.services.feedback import owner_delete_feedback, owner_feedback, send_feedback
from app.services.food import find_food, list_saved_foods, log_food, save_food
from app.services.goals import get_goals, goal_charts, set_goal
from app.services.imports import import_entries
from app.services.lifts import log_lifts
from app.services.log import get_log
from app.services.measurements import log_measurement
from app.services.owner import owner_calls
from app.services.programs import save_program
from app.services.progress import get_progress
from app.services.routines import get_routines, save_routine
from app.services.trends import get_days, get_trends

__all__ = [
    "DUPLICATE_MINUTES", "READ_LIMIT", "WRITE_FUNCTIONS", "WRITE_LIMIT", "WRITE_WINDOW_MINUTES",
    "account_empty", "all_exercises", "delete_account", "delete_entry", "edit_entry", "entry_kinds", "export_data",
    "find_food",
    "get_cardio", "get_data", "get_days", "get_entry", "get_goals", "get_log", "goal_charts", "get_progress",
    "get_routines", "get_trends", "import_entries", "list_exercises", "list_saved_foods",
    "log_bodyweight", "log_cardio", "log_food", "log_lifts", "log_measurement", "owner_calls",
    "owner_delete_feedback", "owner_feedback", "restore_entry", "save_food", "save_program", "save_routine",
    "send_feedback",
    "set_claude_can",
    "set_goal", "update_settings", "what_is_logged", "whoami",
]
