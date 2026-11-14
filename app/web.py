"""The web pages: the home page, sign-in, Today and every day, editing and deleting entries, Progress,
and Settings.

    /                              the landing: what Spotter is, an example exchange and its picture, what you
                                   need, the MCP address and how to connect it
    /sign-in                       sends the person to the provider (signed in: on to /today)
    /auth/callback                 checks `state`, finishes the sign-in, finds or creates the user, on to /today
    /sign-out                      POST: ends the session
    /today                         on to /day/<today in the person's zone>
    /day/{date}                    one day: sessions with sets and bests, the weigh-in, measurements;
                                   prev and next; a weigh-in form and a measurement form (POST to
                                   /day/{date}/weigh-in and /day/{date}/measurement)
    /entries/{kind}/{id}/edit      the fields the kind allows on the web (GET, POST); a saved edit goes back to
                                   the day with "{Kind} saved." and Undo
    /entries/{kind}/{id}/delete    POST: delete, back to the day with "{Kind} deleted." and Undo
    /entries/{kind}/{id}/restore   POST: undo a delete, back to the day; with undo_edit, undo the last edit,
                                   back to the day with "{Kind} edit undone."
    /progress                      one line per lift, the Trends card (get_trends), and the body weight
                                   chart with its smoothed line
    /progress/{lift}               one lift: its chart, bests and sessions
    /goals                         each open goal as its own card with its path chart (goal_charts), the
                                   closed ones (get_goals);
                                   Hit, Drop and Reopen POST to /goals/{goal_id}/status; one form for a new
                                   goal (POST), one kind at a time (?kind=)
    /foods                         the saved foods as a table (list_saved_foods), a fold-out that saves one
                                   (POST, save_food); delete and Undo POST to /foods/{id}/delete and /restore,
                                   and so does the Undo of a food's edit, as an entry's does
    /foods/{id}/edit               one saved food's serving, kcal and grams (GET, POST)
    /settings                      six cards: the account, the time zone (POST), the targets (POST to
                                   /settings/targets), the switch for what Claude may do (POST to
                                   /settings/claude-can), the person's data (export, delete), and
                                   More (Saved foods, and Sign out for a phone). Each post lands on its own card,
                                   and a GET of /settings/targets or /settings/claude-can goes to that card
    /settings/export               POST: a zip of CSVs, one per table the person owns
    /settings/delete               GET: what goes; POST: the sign-in at the provider, then every row here,
                                   then the session, then a plain "deleted" page
    PLACEHOLDERS                   every other page links.py names, until its real page is built

Every page but / and /sign-in needs sign-in. Every web write calls the service Claude's function calls,
then writes a call-log row under the same function name (rule 9 of stage 3), so the write limit counts
web and Claude together. The services' error texts are written for Claude; the pages say what went
wrong in their own words.

The session is Spotter's own signed cookie. After sign-in it holds exactly the user id, the CSRF token,
the provider's session id (for signing out there too) and the sign-in time. No provider token ever
goes into it. A session older than SESSION_DAYS from its sign-in is signed out, however recently the
cookie was renewed. Every non-GET route checks the CSRF token first.

A path no route answers gets the designed 404 page (fallback, mounted last by main.create_app), and a crash
in a page route the designed 500 page (PageErrors), both with the page header. Every web write's redirect
carries a mark (marked), so the page after it is calm.

Every form that writes carries a hidden key as its last field, new each time its page is drawn (form_key). The
same form sent again with the same fields writes nothing and lands where the first send did (logged_write,
contract idempotency.md). Sign out and the export write nothing through a service, and carry none.

Both edit forms carry the entry's updated_at as their page drew it, in a hidden field. The service refuses a
form older than the entry's last change (entry_changed, contract edit-history.md): nothing is saved, and the
page says so (changed_page).

Web sign-in fails closed: without the provider values, a session key or the site address, every page
but / says sign-in is not set up, no session is kept, and MCP is unaffected.

Database calls are synchronous, so they run in a worker thread, never on the event loop. A database that is
not there, or too busy (no free pooled connection, a query or lock wait past its limit), is the not-set-up
page, never a 500.

The page limit: more than PAGE_LIMIT loads of the signed-in pages by one person in PAGE_WINDOW_MINUTES is the
"Too many page loads" page (429), counted in memory (PageLoads). Writes keep the services' write limit.
"""

import hashlib
import inspect
import io
import json
import logging
import math
import re
import secrets
import threading
import time
import zipfile
from collections import deque
from contextvars import ContextVar
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote as url_quote, urlencode, urlsplit, urlunsplit

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.exc import InterfaceError, InternalError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route, request_response
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sqlalchemy import func, select

from app import calllog, charts, csrf, db, formatting, links, mcp_server, rules, services, users, web_auth
from app.config import ROOT, Settings, get_settings
from app.models import (
    CLAUDE_CAN, MEASUREMENT_KINDS, Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, Measurement, SavedFood, User,
)
from app.services import account as account_checks, body, common, lifts, measurements, progress as progress_data
from app.services import goals as goal_rules

log = logging.getLogger("spotter")

HERE = Path(__file__).resolve().parent


def show_filter(value, quantity: str, widened: bool = False, *, unit: bool = True, page: bool = True) -> str:
    """formatting.show for a template: a page by default, so a no-break space goes before the unit.
    {{ x|show("kcal") }}; unit=False for a cell under a header that names the unit; page=False for
    text that keeps a plain space."""
    return formatting.show(value, quantity, page=page, widened=widened, unit=unit)


# The one place numbers and dates reach a template: every figure on a page goes through these filters.
# field gives a form input's value at the decimals the store keeps.
FILTERS = {"show": show_filter, "day": formatting.day, "day_heading": formatting.day_heading,
           "days": formatting.days, "day_utc": formatting.day_utc, "weekday": formatting.weekday,
           "weekday_letter": formatting.weekday_letter, "field": formatting.field}


STYLESHEET = HERE / "static" / "style.css"


def style_version(path: Path = STYLESHEET) -> str | None:
    """The stylesheet's version: the start of the SHA-256 of its bytes. Read once, when the app starts,
    and carried in the stylesheet link's query, so a deploy never serves an old copy from a cache.
    None when the file cannot be read: the pages lose their look, nothing else stops."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        log.warning("The stylesheet could not be read, so its link carries no version.")
        return None


def style_href(path: Path = STYLESHEET) -> str:
    """The stylesheet's address for every page head: with its version, or plain without one."""
    version = style_version(path)
    return "/static/style.css" if version is None else f"/static/style.css?v={version}"


def form_key() -> str:
    """A fresh key for one drawn form (contract idempotency.md, decision 11). Every form that writes carries one
    in a hidden field named key, so the same form sent twice writes once (logged_write)."""
    return secrets.token_urlsafe(16)


def _environment() -> Environment:
    env = Environment(loader=FileSystemLoader(str(HERE / "templates")), autoescape=True)
    env.filters.update(FILTERS)
    env.globals["style_href"] = style_href()
    env.globals["form_key"] = form_key
    return env


templates = Jinja2Templates(env=_environment())

SESSION_COOKIE = "spotter_session"
SESSION_DAYS = 14
SESSION_SECONDS = SESSION_DAYS * 24 * 3600
MIN_SESSION_SECRET = 32  # characters
USER = "user_id"
PROVIDER_SESSION = "provider_session"
SIGNED_IN_AT = "signed_in_at"  # seconds since 1970, set once at sign-in
STATE = "sign_in_state"

# Pages links.py names that are not built yet: name -> route path. Each answers, behind sign-in, with a
# short "not built yet" page, so no link Spotter hands out is a dead end. Delete an entry when its
# real page arrives.
PLACEHOLDERS: dict[str, str] = {}

# The kinds of entry the web shows and edits, and the fields it offers for each (decision 7: sets are
# changed through Claude only in this stage).
EDIT_FIELDS = {
    "bodyweight": ("kg", "date"),
    "measurement": ("value", "date"),
    "lift_session": ("date", "notes"),
    "food_entry": ("description", "kcal", "meal", "date"),
    "cardio_entry": ("exercise", "minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal",
                     "notes", "date"),
}
# The entry kind's one spelling, from one table: "Weigh-in deleted." at the start of a line, "Edit weigh-in"
# inside one.
ENTRY_WORDS = {"bodyweight": "Weigh-in", "measurement": "Measurement", "lift_session": "Lift session",
               "food_entry": "Food entry", "cardio_entry": "Cardio session"}
KIND_NAMES = {kind: words.lower() for kind, words in ENTRY_WORDS.items()}
NUMBER_FIELDS = {"kg", "value", "kcal", "minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr",
                 "machine_kcal", "kcal_per_serving", "grams_per_serving", "calorie_target_kcal",
                 "target_sessions_per_week", "target_cardio_minutes_per_week"}
DATE_ERRORS = {"invalid_date", "date_in_future", "date_too_old", "day_taken"}
# The quantity of each number an edit form offers, for formatting.field: the decimals the store keeps.
FIELD_QUANTITIES = {"kg": "weigh_in", "kcal": "kcal", "minutes": "session_time", "distance_km": "km",
                    "incline_pct": "incline", "speed_kmh": "speed", "avg_hr": "bpm", "machine_kcal": "kcal",
                    "kcal_per_serving": "kcal_per_serving", "grams_per_serving": "grams"}

MEALS = ("breakfast", "lunch", "dinner", "snack")
MEAL_CHOICES = tuple((meal, meal.capitalize()) for meal in MEALS)  # the Food fold-out's Meal choices
# Who supplied a food entry's number (rule 13), in words. An estimate is marked on the page.
SOURCE_WORDS = {"you_said": "you said", "saved_food": "saved food", "usda": "USDA", "claude_estimate": "estimate"}
ATTRIBUTION = ("Calorie estimates use MET values from the 2024 Adult Compendium of Physical Activities "
               "(CC BY-NC-ND 4.0).")
TARGET_FIELDS = ("calorie_target_kcal", "target_sessions_per_week", "target_cardio_minutes_per_week")
TARGET_QUANTITIES = {"calorie_target_kcal": "kcal", "target_sessions_per_week": "count",
                     "target_cardio_minutes_per_week": "week_minutes"}
FOOD_FIELDS = ("name", "serving_label", "kcal_per_serving", "grams_per_serving")

# What a page says beside a field the service refused, in its own words: (kind, field) -> text.
FIELD_HELP = {
    ("food_entry", "description"): "Give a description, up to 200 characters.",
    ("food_entry", "kcal"): "That kcal was not saved: give a whole number from 0 to 5,000.",
    ("food_entry", "meal"): "Choose breakfast, lunch, dinner or snack.",
    ("cardio_entry", "exercise"): "Choose an activity from the list.",
    ("cardio_entry", "minutes"): "Give the minutes, from 1 to 600.",
    ("cardio_entry", "distance_km"): "Give the distance in km, from 0.01 to 300, or leave it empty.",
    ("cardio_entry", "incline_pct"): "Give the incline in %, from 0 to 40, or leave it empty.",
    ("cardio_entry", "speed_kmh"): "Give the speed in km/h, from 0.1 to 60, or leave it empty.",
    ("cardio_entry", "avg_hr"): "Give the average heart rate, 30 to 240, as a whole number, or leave it empty.",
    ("cardio_entry", "machine_kcal"): "Give the machine's number, 1 to 5,000, as a whole number, or leave it empty.",
    ("cardio_entry", "notes"): "Keep the notes under 2,000 characters.",
    # Foods' two forms: the limits are save_food's own (rules.TEXT_LIMITS, rules.NUMBERS). The kcal and grams lines
    # answer a typed comma or unit ("1,000", "58,5", "40g") as well as the range: save_food takes a plain number only.
    ("saved_food", "name"): f"Give the food a name, up to {rules.TEXT_LIMITS['name']} characters.",
    ("saved_food", "serving_label"): (f"Say what one serving is, such as 1 roti, up to "
                                      f"{rules.TEXT_LIMITS['serving_label']} characters."),
    ("saved_food", "kcal_per_serving"): ("Give the kcal in one serving as a plain number with no comma, from "
                                         f"{rules.span(*rules.NUMBERS['kcal_per_serving'][:2], 'kcal_per_serving')}"
                                         " kcal."),
    ("saved_food", "grams_per_serving"): ("Give the grams in one serving as a plain number, such as 40, from "
                                          f"{rules.span(*rules.NUMBERS['grams_per_serving'][:2], 'grams')} g, "
                                          "or leave it empty."),
}
FIRST_FIELD = {"bodyweight": "kg", "measurement": "value", "lift_session": "notes", "food_entry": "description",
               "cardio_entry": "minutes", "saved_food": "name"}
FIELDS_OF = {**EDIT_FIELDS, "saved_food": FOOD_FIELDS}


# The switch's words, the person's only (section L, Settings): its label, then what each setting lets the person's
# assistant do, in one line each, which does not repeat the label drawn right above it. Each line says all that
# mcp_server.NEEDS lets that setting call beyond the one before it: tests/test_web_account.py holds the two side by
# side. The stored values (models.CLAUDE_CAN) never change. No text a connected model reads comes from these tables.
CLAUDE_CAN_LINES = {
    "read": "Your assistant can read what you have logged and change nothing.",
    "log": "Your assistant can also log and import entries, edit and restore them, save foods, set goals, and change "
           "your time zone and targets. It cannot delete.",
    "log_delete": "Your assistant can also delete entries and saved foods. A deleted one can always be restored.",
}
CLAUDE_CAN_LABELS = {"read": "Read only", "log": "Read and log", "log_delete": "Read, log and delete"}

# Settings (section L): each of its three forms posts to its own card's anchor, and every redirect that answers one
# names that anchor itself, so the person lands on the card that says it was saved. The automatic zone save
# (base.html's #auto-time-zone) is not one of them: it posts to /settings too, with `next` only when its page was
# drawn without an error, so the Time zone card's form carries a field of its own (ZONE_CARD_FIELD, its value the
# card's id) and only a post that carries it lands on the card. (the mark's kind, its card's id)
SETTINGS_CARDS = {"time_zone": "zone", "targets": "targets", "claude_can": "assistant"}
ZONE_CARD_FIELD = "card"
# Each card's saved line, by the mark's kind: one at most on a page, in its own card.
SETTINGS_SAVED = {"time_zone": "Time zone saved.", "targets": "Targets saved.",
                  "claude_can": "Your assistant's access saved."}
# What the cards say for a refusal, in the page's own words, by the refusal's code (update_settings and
# set_claude_can give no field). Any other code takes its card's general line. The time zone and the switch say it
# in the card, the targets under each field: their ranges are update_settings' own (rules.NUMBERS).
ZONE_ERROR_WORDS = {code: "That time zone was not recognised, so nothing was changed. Choose your time zone from the "
                          "list." for code in ("unknown_time_zone", "invalid_field")}
# A zone refusal of a post that chose nothing (the list's "Choose your time zone", whose value is empty): the person
# chose no zone, so the line says to choose one, not that their choice was not recognised.
ZONE_ERROR_EMPTY = "Choose your time zone from the list."
ZONE_ERROR_GENERAL = "The time zone was not saved. Choose yours from the list and try again."
SWITCH_ERROR_WORDS = {"invalid_field": "Choose one of the three settings below."}
SWITCH_ERROR_GENERAL = "What your assistant may do was not saved. Choose one of the three and try again."


def _target_span(field: str) -> str:
    lo, hi, _ = rules.NUMBERS[field]
    return rules.span(lo, hi, TARGET_QUANTITIES[field])


# The calorie and cardio lines answer a typed comma ("2,000") as well as the range: update_settings takes a plain
# whole number.
TARGET_ERROR_WORDS = {
    ("invalid_field", "calorie_target_kcal"): ("Give the calories as a whole number with no comma, from "
                                               f"{_target_span('calorie_target_kcal')} kcal, or leave it empty."),
    ("invalid_field", "target_sessions_per_week"): ("Give lift sessions a week as a whole number from "
                                                    f"{_target_span('target_sessions_per_week')}, or leave it empty."),
    ("invalid_field", "target_cardio_minutes_per_week"): ("Give cardio minutes a week as a plain whole number, such "
                                                          "as 150, from "
                                                          f"{_target_span('target_cardio_minutes_per_week')}, or leave "
                                                          "it empty."),
}
TARGETS_ERROR_GENERAL = "The targets were not saved. Check each field and try again."

# Goals (section L, Set a goal). The form shows one kind at a time, chosen by four segments, each a link to
# /goals?kind={kind}#set-goal. The kind in the address and in the form's hidden field is set_goal's own word for
# it. (kind, the segment's words), in the segments' order.
GOAL_KINDS = (("bodyweight", "Body weight"), ("lift", "Lift"), ("cardio_best", "Cardio time"),
              ("measurement", "Measurement"))
GOAL_KIND_FIRST = "bodyweight"  # current when the address names no kind, or one the page does not know
GOAL_MEASUREMENTS = ("waist", "hips", "chest", "arm", "thigh", "resting_hr")  # decision 2: states, not daily amounts
GOAL_FIELDS = ("kind", "exercise", "measurement_kind", "distance_km", "target_value", "target_date", "start_value",
               "text")
# Each kind's fields, in the form's order. Where you are now (start_value) joins them before the target only when
# set_goal refused for want of a start, or the person typed one.
GOAL_KIND_FIELDS = {"bodyweight": ("target_value", "target_date", "text"),
                    "lift": ("exercise", "target_value", "target_date", "text"),
                    "cardio_best": ("exercise", "distance_km", "target_value", "target_date", "text"),
                    "measurement": ("measurement_kind", "target_value", "target_date", "text")}
# What the form says for each set_goal refusal a person can reach from it, in the page's own words, keyed by the
# refusal's error code and its field: never by its message. A line that differs by kind is a {kind: line} table.
# A measurement's range line names the measurement chosen: {what}, {unit}, {span} and {whole} are filled from
# rules.MEASUREMENTS. The ranges are the service's own (rules, services.goals), never typed here.
_BODYWEIGHT_SPAN = rules.span(*rules.BODYWEIGHT_KG, "goal_target")
_LIFT_MOST = formatting.show(goal_rules.TARGETS["lift"][1], "goal_target", unit=False)
_TIME_SPAN = rules.span(*goal_rules.TARGETS["cardio_best"], "goal_target")
_DISTANCE_SPAN = rules.span(*goal_rules.DISTANCE_KM, "goal_target")
_KIND_LINE = "Choose a kind of goal above: Body weight, Lift, Cardio time or Measurement."
_EXERCISE_LINE = {"lift": "Choose a lift from the list.", "cardio_best": "Choose an activity from the list."}
_MEASUREMENT_LINE = "Choose a measurement from the list."
_DAILY = [measurements.kind_word(k).lower() for k in goal_rules.DAILY_AMOUNTS]  # steps, sleep, water: not goals
_DAILY_WORDS = (", ".join(_DAILY[:-1]) + " and " + _DAILY[-1]).capitalize()
GOAL_ERROR_WORDS: dict[tuple[str, str], str | dict[str, str]] = {
    ("missing_field", "kind"): _KIND_LINE,
    ("invalid_field", "kind"): _KIND_LINE,
    ("missing_field", "exercise"): _EXERCISE_LINE,
    ("unknown_exercise", "exercise"): _EXERCISE_LINE,
    ("invalid_field", "exercise"): _EXERCISE_LINE,
    ("missing_field", "distance_km"): "Give the distance the time is for, in km.",
    ("invalid_field", "distance_km"): f"Give the distance in km, from {_DISTANCE_SPAN}.",
    ("missing_field", "measurement_kind"): _MEASUREMENT_LINE,
    ("invalid_kind", "measurement_kind"): _MEASUREMENT_LINE,
    ("invalid_field", "measurement_kind"): f"{_MEASUREMENT_LINE} {_DAILY_WORDS} are daily amounts, not goals to "
                                           "reach by a date.",
    ("missing_field", "target_value"): {
        "bodyweight": "Give the weight you aim for, in kg.",
        "lift": "Give the estimated max you aim for, in kg.",
        "cardio_best": "Give the time you aim for, in minutes.",
        "measurement": "Give a target for your {what}, in {unit}."},
    ("invalid_field", "target_value"): {
        "bodyweight": f"Give the weight you aim for in kg, from {_BODYWEIGHT_SPAN}.",
        "lift": f"Give the estimated max you aim for in kg, more than 0 and at most {_LIFT_MOST}.",
        "cardio_best": f"Give the time you aim for in minutes, from {_TIME_SPAN}.",
        "measurement": "Give a target for your {what} in {unit}, from {span}{whole}."},
    # A new body weight or measurement goal takes its direction from the target, so only a lift or a cardio time
    # can point the wrong way from the form. Each line holds with the start inside its range and at its end, where
    # no target the other side can be set, so it tells no one to do what cannot be done.
    ("wrong_direction", "target_value"): {
        "lift": "A lift goal aims above your estimated max now, and this target is not above it.",
        "cardio_best": "A cardio time goal aims below your time now, and this target is not below it."},
    ("target_is_start", "target_value"): "That target is where you are now: give one above or below it.",
    ("missing_field", "target_date"): "Choose the day you aim to reach it by.",
    ("invalid_date", "target_date"): "That is not a day: choose one after today.",
    ("date_not_in_future", "target_date"): "Choose a day after today.",
    ("missing_field", "start_value"): {
        "bodyweight": "Nothing is logged to start from yet: give your weight now, or log a weigh-in first.",
        "lift": "No session of this lift is logged yet: give your estimated max now, or log a session first.",
        "cardio_best": "No session of this activity at this distance or longer is logged yet: give your time for it "
                       "now, or log one first.",
        "measurement": "No {what} measurement is logged yet: give where you are now, or log one first."},
    ("invalid_field", "start_value"): {
        "bodyweight": f"Give your weight now in kg, from {_BODYWEIGHT_SPAN}.",
        "lift": f"Give your estimated max now in kg, more than 0 and at most {_LIFT_MOST}.",
        "cardio_best": f"Give your time now in minutes, from {_TIME_SPAN}.",
        "measurement": "Give your {what} measurement now, in {unit}, from {span}{whole}."},
    ("invalid_field", "text"): f"Keep the notes to {formatting.show(rules.TEXT_MAX, 'count')} characters or fewer, "
                               "in plain text.",
}
# A line that names the measurement, when none the page knows is chosen: by (code, field). A line with none here
# then shows the general line at the top.
GOAL_ERROR_UNNAMED = {("missing_field", "target_value"): "Give the target you aim for."}
# A refusal the table does not know, by its code or its field: still something true, at the top of the form.
GOAL_ERROR_GENERAL = "That goal was not set. Check each field and try again."
GOAL_SAVED = {"goal": "Goal set.", "hit": "Goal marked as hit.", "dropped": "Goal dropped.", "open": "Goal reopened."}
# The fragment every redirect that answers a form low on its page ends in, Goals' Set a goal, Foods' Save a food and
# the day page's fold-outs:
# the id base.html gives body. Without one, the address after the redirect keeps the post's own fragment
# (#set-goal, #save-food) and lands on the form, out of sight of the strip. The status posts (Hit, Drop, Reopen), a
# saved food's edit page, Delete and Undo carry no fragment, so their redirects carry none.
POST_LANDS = "#top"
GOAL_POST_LANDS = POST_LANDS  # goals.html names it
# set_goal's warnings, said in the page's own words. A saved goal's warnings travel across the redirect as
# these codes (as the day page carries its undo): (code, how the warning starts, what the page says).
GOAL_NOTES = (
    ("twice", "This goal was already set", "This goal was already set, so it was not set twice."),
    ("duplicate", "There is already an open goal",
     "You already had an open goal like this one. Both are kept: drop the one you no longer want."),
    ("start", "start_value was ignored",
     "The goal starts from what you have logged, not from the number you typed for where you are now."),
    ("past_date", "Reopened, but its target date",
     "Its date has passed, so there is nothing to aim at: set a new goal with a later date."),
    ("ignored", None, "Fields this kind of goal does not use were left out."),
)
GOAL_NOTE_TEXT = {code: text for code, _, text in GOAL_NOTES}
CLOSED_WORDS = {"hit": "Hit", "dropped": "Dropped"}  # a closed goal's tag (section L, Closed goals)
STATUS_CLASSES = {"reached": "tag-good", "on track": "tag-good", "behind": "tag-behind",
                  "not enough data": "tag-nodata"}

# What Delete my account says goes, by count: the one table of its words (a cardio entry is a "Cardio session", as
# ENTRY_WORDS spells it). Deleted rows count: they go too.
DELETE_COUNTS = ((LiftSession, "Lift sessions"), (Bodyweight, "Weigh-ins"), (Measurement, "Measurements"),
                 (FoodEntry, "Food entries"), (CardioEntry, "Cardio sessions"), (SavedFood, "Saved foods"),
                 (Goal, "Goals"))

ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

PAGE_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                               "base-uri 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}
HSTS = {"Strict-Transport-Security": "max-age=31536000"}  # https sites only
# /static only: an hour in the browser's cache, so the page cross-fade finds the stylesheet there. The
# stylesheet's address carries its version, so a deploy is never hidden behind a cached copy.
STATIC_CACHE = "public, max-age=3600"


# ---------------------------------------------------------------- set-up

def problem(settings: Settings) -> str | None:
    """Why web sign-in is off, or None when it is on. Names settings, never their values."""
    if settings.problem is not None:
        return "The server's sign-in settings (AUTHKIT_DOMAIN, MCP_RESOURCE_URL) are missing or invalid."
    if not settings.web_sign_in_configured:
        return "WORKOS_CLIENT_ID and WORKOS_API_KEY are not both set."
    if settings.session_secret is not None and len(settings.session_secret) < MIN_SESSION_SECRET:
        return f"SESSION_SECRET is too short: use at least {MIN_SESSION_SECRET} random characters."
    if settings.site_origin is None or settings.session_key is None:
        return "There is no key to sign the session cookie with."
    return None


def headers_for(settings: Settings, static: bool = False) -> dict[str, str]:
    """The fixed headers for web responses; HSTS only when the site is served over https. /static is
    the same but for its cache line: pages are never stored, static files for an hour."""
    values = dict(PAGE_HEADERS)
    if static:
        values["Cache-Control"] = STATIC_CACHE
    if (settings.site_origin or "").startswith("https://"):
        values.update(HSTS)
    return values


class SecurityHeaders:
    """Adds fixed headers to every response of the route it wraps, unless the response set its own."""

    def __init__(self, app: ASGIApp, headers: dict[str, str]) -> None:
        self.app = app
        self.headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in self.headers if k not in present]
            await send(message)

        await self.app(scope, receive, send_with_headers)


class PageErrors:
    """A crash in a page route answers with the designed 500 page, then the error is raised again, so it
    is still logged and a test client that raises on server errors still raises. The page uses the
    signed-out frame, since the failure may be in the session itself. It sits inside SecurityHeaders, so
    the page carries the page header, and outside the session, so no cookie is set on the way out and the
    session survives. An HTTPException passes untouched: a malformed form is a 400, not a 500. Nothing is
    sent once a response has started."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def watch(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watch)
        except HTTPException:
            raise
        except Exception:
            if not started:
                await self.send_page(scope, receive, send)
            raise

    @staticmethod
    async def send_page(scope: Scope, receive: Receive, send: Send) -> None:
        # The request without its session and its state: the signed-out frame.
        bare = {key: value for key, value in scope.items() if key not in ("session", "state")}
        try:
            response = server_error_page(Request(bare, receive))
        except Exception as exc:  # noqa: BLE001 - the page could not be drawn: the plain 500 stands
            log.warning("The 500 page could not be drawn: %s", type(exc).__name__)
            return
        try:
            await response(bare, receive, send)
        except Exception as exc:  # noqa: BLE001 - the crash is what is raised, never the failed send
            log.warning("The 500 page could not be sent: %s", type(exc).__name__)


# ---------------------------------------------------------------- helpers

class DatabaseUnavailable(Exception):
    """The database is not there (not configured, or not reachable). Shown as the plain not-set-up page."""


# Only these mean "the database is not there", or too busy to answer: no free pooled connection in its wait
# (PoolTimeout), a statement or lock wait cancelled by its limit (an OperationalError), or a transaction left
# idle past its limit (an InternalError). The same set as MCP's. Any other error is a bug and surfaces as a 500.
# The engine hides bound values, so a logged statement never carries a person's data.
DATABASE_DOWN = (OperationalError, InterfaceError, InternalError, PoolTimeout, db.DatabaseNotConfigured)


async def _db(function, *args):
    """Run a synchronous database call in a worker thread."""
    try:
        return await run_in_threadpool(function, *args)
    except DATABASE_DOWN as exc:
        log.warning("Web page could not use the database: %s", type(exc).__name__, exc_info=True)
        raise DatabaseUnavailable from exc


def _load_user(user_id: int) -> User | None:
    with db.session_scope() as session:
        return session.get(User, user_id)


def _fresh(signed_in_at: object) -> bool:
    """True when the sign-in time is a real time less than SESSION_DAYS ago (a minute's grace for a
    clock step, never further in the future)."""
    if not isinstance(signed_in_at, int) or isinstance(signed_in_at, bool):
        return False
    age = time.time() - signed_in_at
    return -60 <= age < SESSION_SECONDS


async def current_user(request: Request) -> User | None:
    """The signed-in person, looked up once per request. A session that is too old, has no usable
    sign-in time, or names a user that no longer exists is cleared. Without web sign-in there is no
    session and nobody is signed in."""
    if "session" not in request.scope:
        return None
    if hasattr(request.state, "user"):
        return request.state.user
    user_id = request.session.get(USER)
    user = None
    if (isinstance(user_id, int) and not isinstance(user_id, bool)
            and _fresh(request.session.get(SIGNED_IN_AT))):
        user = await _db(_load_user, user_id)
    if user is None and user_id is not None:
        request.session.clear()
    request.state.user = user
    return user


# The page frame (section L, Navigation and Hooks). The four tabs, in order: (tab, path, label).
TABS = (("today", "/today", "Today"), ("progress", "/progress", "Progress"), ("goals", "/goals", "Goals"),
        ("settings", "/settings", "Settings"))
# Each template's hooks on main.page: (page class, width class, current tab). A view can pass page_class,
# width_class or current_tab itself to override its row. A template not listed gets none of the three.
FRAMES = {
    "home.html": ("landing", None, None),
    "day.html": ("today", None, "today"),
    "edit.html": (None, "form-page", "today"),
    "progress.html": ("progress", None, "progress"),
    "progress_lift.html": ("lift-page", None, "progress"),
    "goals.html": ("goals-page", None, "goals"),
    "foods.html": ("foods-page", None, "settings"),
    "food_edit.html": (None, "form-page", "settings"),
    "settings.html": ("settings-page", None, "settings"),
    "delete_account.html": (None, "notice-page", "settings"),
    "message.html": (None, "notice-page", None),
}
# The address marks of a page after a write (section L, The calm page): any of these, whatever its value,
# makes the page calm, and so does a form sent back with its error.
CALM_MARKS = ("saved", "deleted", "restored", "notes", "kind", "add")


def is_calm(request: Request, status_code: int) -> bool:
    """True on a page after a write: a POST answered with its form error (400), or an address mark."""
    if request.method == "POST" and status_code == 400:
        return True
    return any(mark in request.query_params for mark in CALM_MARKS)


def frame(request: Request, name: str, status_code: int, signed_in: bool, context: dict) -> dict:
    """The frame's context keys: main_class for main.page, current_tab for aria-current, calm for the body."""
    page_class, width_class, tab = FRAMES.get(name, (None, None, None))
    page_class = context.pop("page_class", page_class)
    width_class = context.pop("width_class", width_class)
    tab = context.pop("current_tab", tab)
    calm = context.pop("calm", is_calm(request, status_code))
    classes = ["page", page_class]
    # A past day, or a day after today: the Today card spans both columns on a laptop.
    if (page_class == "today" and context.get("on") is not None and context.get("today") is not None
            and context["on"] != context["today"]):
        classes.append("past")
    classes += [width_class, None if signed_in else "out"]
    return {"main_class": " ".join(c for c in classes if c), "current_tab": tab if signed_in else None,
            "tabs": TABS, "calm": calm}


def page(request: Request, name: str, status_code: int = 200, **context) -> Response:
    settings = get_settings()
    user = getattr(request.state, "user", None)
    shown = frame(request, name, status_code, user is not None, context)
    values = {
        **shown,
        "signed_in": user is not None,
        "web_ready": problem(settings) is None,
        "mcp_url": settings.mcp_resource_url if settings.configured else None,
        "csrf_token": csrf.token(request) if user is not None else "",
        # Signed in with no zone yet: the page carries the hidden form and the script that saves the
        # browser's own zone, once.
        "time_zone_missing": user is not None and not user.time_zone,
        # Where the automatic zone save brings the person back to. Never set on an error page, so a
        # 404 never repeats the address it was given.
        "next_path": request.url.path if status_code < 400 else None,
        # Rule 15: the one footer line naming the Compendium, on a page that shows a cardio estimate only.
        "attribution": ATTRIBUTION if context.pop("shows_estimate", False) else None,
        # The person's own date, for the date filters. A view passes the date its read was worked out on.
        "today": users.today_for(user) if user is not None and "today" not in context else None,
        **context,
    }
    return templates.TemplateResponse(request, name, values, status_code=status_code)


def message(request: Request, title: str, text: str, fix: str | None = None, status_code: int = 200,
            home_only: bool = False) -> Response:
    """Every message the web sends, in one template: the title, the message, the fix, and the links (Today
    signed in; Home, and Sign in when web sign-in is set up, signed out; Home alone with home_only)."""
    return page(request, "message.html", status_code=status_code, title=title, message=text, fix=fix,
                home_only=home_only)


def redirect(address: str) -> Response:
    return RedirectResponse(address, status_code=303)


# The mark a post that changed nothing carries: the page after it is calm, and no page has words for it, so
# nothing says a thing was saved (section L, The calm page).
UNCHANGED = ("saved", "unchanged")
# The marks /today passes on to the day address.
DAY_MARKS = ("saved", "deleted", "restored")


def marked(address: str, mark: str, value: str) -> str:
    """The address with one mark added to its query, before any fragment. Every web write's redirect
    carries a mark, so the page after it is calm."""
    parts = urlsplit(address)
    query = "&".join(q for q in (parts.query, urlencode({mark: value}, safe=":,")) if q)
    return urlunsplit(parts._replace(query=query))


def sign_in_failed(request: Request) -> Response:
    return message(request, "Sign-in failed", "Spotter could not finish signing you in.",
                   "Try again from the sign-in page.", status_code=400)


def not_set_up(request: Request, reason: str | None = None) -> Response:
    return message(request, "Not set up yet", "Sign-in on the web is not set up on this server yet.",
                   reason or "The server needs its settings completed. MCP is unaffected.", status_code=503)


async def refuse(request: Request) -> Response:
    """The 403 for a form whose token did not match. The person is looked up first, so a signed-in person
    keeps the signed-in frame."""
    await current_user(request)
    return message(request, "Form expired", "This form could not be checked, so nothing was changed.",
                   "Go back, reload the page and try again.", status_code=403)


def not_found_page(request: Request) -> Response:
    """The one 404: it never says whether the thing exists for someone else, or repeats the address."""
    return message(request, "Page not found", "There is nothing here.", "Go back to Today and start from there.",
                   status_code=404)


def server_error_page(request: Request) -> Response:
    """The designed 500, sent by PageErrors on a crash, in the signed-out frame."""
    return message(request, "Something went wrong",
                   "Spotter could not finish this. If you were saving something, it may not be saved.",
                   "Check the page and try again.", status_code=500, home_only=True)


async def unrouted(request: Request) -> Response:
    """A path no route answers: the 404 page. It carries no session, so it is signed out."""
    return not_found_page(request)


# /mcp and /.well-known, and anything under either, are the MCP app's: never a page.
MCP_PATHS = ("/mcp", "/.well-known")


def mcp_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in MCP_PATHS)


def route_path(scope: Scope) -> str:
    """The path as the router reads it: without the root_path prefix the app is served under. A path that
    only looks alike ("/xy/mcp" under "/x") keeps its prefix."""
    path, root = scope["path"], scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root):] if path[len(root)] == "/" else path


def fallback(settings: Settings, mcp_app: ASGIApp) -> ASGIApp:
    """Mounted at "/", last: /mcp, /.well-known and anything under them go to mcp_app untouched. Every other
    path no route answered, by any method, gets the designed 404 with the page header."""
    page_app = SecurityHeaders(PageErrors(request_response(unrouted)), headers_for(settings))

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        # The path as the router reads it, without any root_path prefix the app is served under.
        if scope["type"] == "http" and not mcp_path(route_path(scope)):
            await page_app(scope, receive, send)
        else:
            await mcp_app(scope, receive, send)

    return app


def too_many(request: Request, result: dict) -> Response:
    wait = result.get("wait_minutes")
    when = (f"in {formatting.show(wait, 'count')} minute{'s' if wait != 1 else ''}"
            if isinstance(wait, int) and wait > 0 else "in a few minutes")
    return message(request, "Too many changes", "There have been too many changes to this account in the last "
                   "10 minutes, so nothing was saved.", f"Try again {when}.", status_code=429)


# ---------------------------------------------------------------- the page limit

# More than PAGE_LIMIT page loads (GET, and HEAD, which is a GET without its body) of the signed-in pages by one
# person in PAGE_WINDOW_MINUTES is refused with the "Too many page loads" page. A page read writes no call-log
# row, so the count lives in memory: one PageLoads per app (routes() makes it), so per process, keyed by the
# person's id. The signed-out pages, /health, /static and the MCP endpoint are never counted. A refused load is
# not counted, so the wait is until the oldest counted load leaves the window. Writes keep their write limit.
PAGE_LIMIT = 300
PAGE_WINDOW_MINUTES = 10
PAGE_METHODS = ("GET", "HEAD")
PAGE_SWEEP_AT = 1024  # people tracked before the ones with no load left in the window are forgotten


def page_clock() -> float:
    """The page limit's clock, in seconds. Tests replace it."""
    return time.monotonic()


class PageLoads:
    """Each signed-in person's page loads in the last PAGE_WINDOW_MINUTES, in this process's memory."""

    def __init__(self) -> None:
        self._loads: dict[int, deque[float]] = {}
        self._lock = threading.Lock()  # the pages run on one event loop today; the lock keeps it right if not

    def take(self, user_id: int) -> int | None:
        """Count one load and return None; or, when it would be more than PAGE_LIMIT, count nothing and return
        the minutes until the oldest counted load leaves the window, at least 1."""
        now = page_clock()
        window = PAGE_WINDOW_MINUTES * 60
        with self._lock:
            loads = self._loads.setdefault(user_id, deque())
            while loads and loads[0] <= now - window:
                loads.popleft()
            if len(loads) >= PAGE_LIMIT:
                return max(1, math.ceil((loads[0] + window - now) / 60))
            loads.append(now)
            if len(self._loads) > PAGE_SWEEP_AT:
                self._loads = {k: v for k, v in self._loads.items() if v and v[-1] > now - window}
            return None


def too_many_pages(request: Request, wait: int) -> Response:
    response = message(request, "Too many page loads",
                       f"There have been more than {formatting.show(PAGE_LIMIT, 'count')} page loads on this account "
                       f"in the last {PAGE_WINDOW_MINUTES} minutes.",
                       f"Wait {formatting.show(wait, 'count')} minute{'s' if wait != 1 else ''}, then reload.",
                       status_code=429)
    response.headers["Retry-After"] = str(wait * 60)
    return response


def page_limited(endpoint, loads: PageLoads):
    """The endpoint behind the page limit: a signed-in person's GET is counted first, and refused past the limit.
    Anything else (a signed-out visitor, a POST) goes straight through."""
    async def run(request: Request) -> Response:
        if request.method in PAGE_METHODS:
            user = await current_user(request)
            if user is not None:
                wait = loads.take(user.id)
                if wait is not None:
                    return too_many_pages(request, wait)
        return await endpoint(request)

    run.__name__ = endpoint.__name__
    return run


def failed(result: dict) -> bool:
    return "error" in result or "errors" in result


def codes(result: dict) -> set[str]:
    """Every error code in a service result, a combined one included."""
    found = {result["error"]} if isinstance(result.get("error"), str) else set()
    return found | {e.get("error") for e in result.get("errors") or [] if isinstance(e, dict)}


def web_sent(write, args: tuple) -> dict:
    """The service's arguments as an assistant's call would send them: the form's fields at the top level, beside
    the entry's kind and entry_id, each named by the service's own parameters. The user id, `by` and an edit
    form's drawn_at are left out."""
    try:
        bound = list(inspect.signature(write).bind(*args).arguments.items())[1:]
    except (TypeError, ValueError):  # not a service's signature: nothing to name
        return {}
    sent: dict = {}
    for name, value in bound:
        if name not in ("by", "drawn_at"):
            sent.update(value if isinstance(value, dict) else {name: value})
    return sent


# The posted form's key (the hidden field form_key() filled), held for the request's writes: with_form_key sets it,
# and logged_write reads its worker thread's copy.
_FORM_KEY: ContextVar[object] = ContextVar("spotter_form_key", default=None)


def with_form_key(endpoint):
    """The endpoint with its posted form's key held for the writes it makes. After the CSRF check, which has
    parsed the form."""
    async def run(request: Request) -> Response:
        posted = request.method not in csrf.SAFE_METHODS
        held = _FORM_KEY.set((await request.form()).get("key") if posted else None)
        try:
            return await endpoint(request)
        finally:
            _FORM_KEY.reset(held)

    run.__name__ = endpoint.__name__
    return run


def kept_text(result: dict) -> str | None:
    """A web write's result as the text kept under its key, the text MCP would send. None when it is not JSON."""
    try:
        return mcp_server._json_text(result)[0]
    except (TypeError, ValueError):
        return None


def logged_write(user_id: int, function: str, write, *args):
    """Run a service write with its call-log row under `function`, the name MCP uses for the same write, and
    its trace (app/calllog.py). The row is stored (in progress) before the service runs, so the write limit
    counts this call while it runs, and completed afterwards. Runs in the worker thread with the write. A
    crash completes the row as internal_error, a lost database as database_unavailable, and is raised.

    A form's key makes the write a keyed one (services.common.keyed). The write's own key is worked out: the
    start of the SHA-256 of the form's key and the write's fingerprint. So the same form sent again with the
    same fields gets the first result back and its row reads repeat, a form sent again with a field changed is
    a new write, and two writes of one form have two keys. A missing or malformed form key: no key, as before."""
    started_at, start = db.utcnow(), time.monotonic()
    row_id = None
    notes = calllog.new_notes()
    sent, form, claim, kept = web_sent(write, args), _FORM_KEY.get(), None, None
    if common.is_key(form):
        mark = common.fingerprint(function, sent)
        claim = common.new_claim(function, hashlib.sha256(f"{form}:{mark}".encode()).hexdigest()[:40], mark)
        sent = {**sent, "key": True}  # for tried: the row says a key was sent, and never holds its value
    try:
        row_id = calllog.start(function, started_at, user_id, "web", calllog.tried(function, sent))
    except Exception as exc:  # noqa: BLE001 - a lost log row never costs the person their write
        log.warning("Web %s: the call log could not be started: %s", function, type(exc).__name__)
    result, code, frame = None, "internal_error", None
    try:
        if claim is None:
            result = write(*args)
        else:
            result, kept = common.keyed(user_id, claim, lambda: write(*args))
            result = json.loads(kept) if kept is not None else result
        return result
    except BaseException as exc:
        code = "database_unavailable" if isinstance(exc, DATABASE_DOWN) else code
        frame = calllog.frame_of(exc)
        if isinstance(exc, Exception) and not isinstance(exc, DATABASE_DOWN):  # type and frames, never the message
            log.error("Web %s crashed: %s", function, calllog.crash_text(exc))
        raise
    finally:
        ms = int((time.monotonic() - start) * 1000)
        if result is None:
            ok, size = False, 0
        else:
            ok = not failed(result)
            code = None if ok else str(result.get("error") or "invalid")
            size = len(json.dumps(result, default=str).encode("utf-8"))
            if claim is not None and claim["row"] is not None:  # this write claimed its key: keep or free it
                common.settle(user_id, claim, result, kept_text(result))
        try:
            calllog.finish(row_id, ms, ok, code, size, calllog.outcome(result, True, kept is not None),
                           calllog.step(code), frame, notes)
        except Exception as exc:  # noqa: BLE001 - a lost log row never costs the person their write
            log.warning("Web %s: the call log could not be finished: %s", function, type(exc).__name__)


class AlreadyDone(Exception):
    """A form sent again whose first send was done and whose result was not kept (already_done). Shown as its own
    page, whichever form it was."""


async def write(user: User, function: str, service, *args) -> dict:
    """A web write: its call-log row, the service, the row completed, in a worker thread."""
    result = await _db(logged_write, user.id, function, service, *args)
    if result.get("error") == "already_done":
        raise AlreadyDone
    return result


def local_path(value: object) -> str | None:
    """A path on this site to send someone back to: exactly one leading slash, no scheme, no host,
    no backslash and no control character. Anything else is None."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or len(value) > 512:
        return None
    if "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    parts = urlsplit(value)
    return value if not parts.scheme and not parts.netloc else None


def parse_day(text: str) -> date | None:
    """A date in a page address: exactly YYYY-MM-DD and a real day after the first one there is (that
    one has no day before it to link to), else None."""
    if not ISO_DAY.fullmatch(text or ""):
        return None
    try:
        on = date.fromisoformat(text)
    except ValueError:
        return None
    return on if on > date.min else None


def day_path(on: date | str) -> str:
    return "/day/" + (on.isoformat() if isinstance(on, date) else str(on))


# ---------------------------------------------------------------- the time-zone list

@lru_cache(maxsize=1)
def zone_groups() -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    """The Settings list: (region, ((name, label), ...)), sorted by region then city, UTC last under
    Other. The names are users.current_zone_names(), the same data that checks and stores a zone, so
    the list, the browser's zone and Claude's can never disagree."""
    regions: dict[str, list[tuple[str, str]]] = {}
    for name in users.current_zone_names():
        region, _, city = name.partition("/")
        label = (city or region).replace("_", " ").replace("/", " / ")
        regions.setdefault(region if city else "Other", []).append((name, label))
    return tuple((region, tuple(sorted(regions[region], key=lambda item: item[1])))
                 for region in sorted(regions, key=lambda r: (r == "Other", r)))


def zone_names() -> tuple[str, ...]:
    """Every zone name the Settings list offers."""
    return tuple(name for _, zones in zone_groups() for name, _ in zones)


def zone_choices(current: str | None) -> tuple[tuple, str | None]:
    """The grouped list and the name to show as selected: the stored zone in its canonical form. A
    stored name the list lacks is offered on its own, selected."""
    groups = zone_groups()
    if not current:
        return groups, None
    selected = users.canonical_zone(current)
    if selected in set(zone_names()):
        return groups, selected
    return (("Saved", ((current, current.replace("_", " ")),)), *groups), current


def _db_safe(endpoint):
    async def run(request: Request) -> Response:
        try:
            return await endpoint(request)
        except DatabaseUnavailable:
            return not_set_up(request, "Spotter cannot reach its database right now.")
        except AlreadyDone:
            return message(request, "Already done", "This form was already sent, and what it asked for was done then.",
                           "Nothing was done a second time. Go back and reload the page to see it.", status_code=409)

    run.__name__ = endpoint.__name__
    return run


# ---------------------------------------------------------------- pages

# The landing page (section L, Landing page). The one line and the second line: the first screen's words,
# and the page's description and link-preview text.
LANDING_LINES = ("A fitness log you keep by talking to your assistant.",
                 "Tell your assistant what you lifted, ate or weighed. Spotter stores it and works out your bests, "
                 "trends and goal pace in code, and your assistant quotes those numbers.")
# The link preview's own words, beside LANDING_LINES. No og:image: no platform documents an SVG as a preview picture.
LANDING_PREVIEW = {"title": "Spotter", "type": "website"}
# The README's "Your assistant" table, where each assistant's steps and plan limits live.
README_TABLE = links.REPOSITORY + "#your-assistant"
# The landing's list of assistants: the README table's rows, in its order (tests/test_web.py holds the two equal).
# The page never reads the README. The one place the page names products, so the one place it says Claude.
ASSISTANTS = ("Claude apps", "Claude Code", "ChatGPT", "Cursor", "VS Code with Copilot", "Gemini app", "Mistral",
              "goose")

# The example exchange of What it looks like. The landing has no account to ask, so it is this one constant: what
# get_goals gives the four-week account on the fixed day (2026-11-10, evals/four_weeks.py), said by the MCP server's
# own reply rules (mcp_server.REPLY_RULE): one fact a line, each goal's plan_line as written, no figure worked out.
# The label keeps a no-break space before its unit, the dates are day() of get_goals' own. tests/test_pages_text.py
# rebuilds every field from get_goals and fails on one character's difference.
LANDING_EXCHANGE = {
    "you": "You",
    "assistant": "Your assistant",
    "question": "Am I on track for my goals?",
    "opening": "All three goals are on track by projection.",
    "goals": (
        {"label": "Body weight 77\u00a0kg", "by": "Tue 5 Jan 2027",
         "plan_line": "79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind.", "reached": "Sat 26 Dec"},
        {"label": "Back Squat 120\u00a0kg", "by": "Tue 2 Feb 2027",
         "plan_line": "107.9 kg now against 99.8 kg on the plan line today: 8.1 kg ahead.", "reached": "Thu 26 Nov"},
        {"label": "Outdoor Run 5\u00a0km in 27:00", "by": "Tue 5 Jan 2027",
         "plan_line": "29:30 now against 29:48 on the plan line today: 18 seconds ahead.", "reached": "Sat 12 Dec"},
    ),
}
# The picture: the body weight goal's Goals card on the fixed day, built once by a script. Its address, its own
# width and height (tests hold them to the file), and its short alt.
LANDING_PICTURE = {"src": "/static/landing-goals.svg", "width": 340, "height": 375,
                   "alt": "The Goals card the assistant quoted, on an example account"}


def exchange_answer(exchange: dict = LANDING_EXCHANGE) -> list[list[str]]:
    """Your assistant's answer, a paragraph each of lines: the opening line, then three lines a goal. Every word is
    the exchange's own, as written: nothing here works one out."""
    return [[exchange["opening"]]] + [[f"{g['label']} by {g['by']}.", g["plan_line"],
                                       f"At this pace it is reached on {g['reached']}."] for g in exchange["goals"]]


# The two counts read once, when the app starts (section L, Where the counts come from). The new-account cap is read
# at each render, from the settings.
CASES_FILE = ROOT / "evals" / "cases.json"


def eval_case_count(path: Path = CASES_FILE) -> int | None:
    """The number of cases in the eval cases file: a JSON list of cases, each an object. None when the file cannot be
    read or holds anything else: the page then leaves its line out, never a typed or a guessed number."""
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("The eval cases file could not be read, so the landing leaves its line out.")
        return None
    if not isinstance(cases, list) or not cases or not all(isinstance(case, dict) for case in cases):
        log.warning("The eval cases file is not a list of cases, so the landing leaves its line out.")
        return None
    return len(cases)


FUNCTION_COUNT = len(mcp_server.NEEDS) - 1  # every function but owner_calls, which only the owner is offered
EVAL_CASES = eval_case_count()


async def home(request: Request) -> Response:
    await current_user(request)
    settings = get_settings()
    return page(request, "home.html", landing_lines=LANDING_LINES, preview=LANDING_PREVIEW,
                site_address=links.page("home") if settings.site_origin else None,
                source_link=links.REPOSITORY, readme_table=README_TABLE, exchange=LANDING_EXCHANGE,
                answer=exchange_answer(), picture=LANDING_PICTURE, signups_per_day=settings.signups_per_day,
                function_count=FUNCTION_COUNT, eval_cases=EVAL_CASES, assistants=ASSISTANTS,
                switch=[CLAUDE_CAN_LABELS[value] for value in CLAUDE_CAN],
                switch_start=CLAUDE_CAN_LABELS[User.__table__.c.claude_can.default.arg],
                site_foot={"source": links.REPOSITORY, "contact": links.REPOSITORY + "/issues"})


async def sign_in(request: Request) -> Response:
    if await current_user(request) is not None:
        return redirect(links.page("today"))
    state = secrets.token_urlsafe(32)
    request.session[STATE] = state
    try:
        address = web_auth.sign_in_url(state)
    except Exception as exc:
        log.warning("Web sign-in could not start: %s", type(exc).__name__)
        return sign_in_failed(request)
    return redirect(address)


async def callback(request: Request) -> Response:
    expected = request.session.pop(STATE, None)  # single use, whatever happens next
    params = request.query_params
    code = params.get("code")
    if "error" in params or not code or not csrf.same(params.get("state"), expected):
        return sign_in_failed(request)
    try:
        identity = await web_auth.exchange(code)
    except Exception:  # web_auth has logged the reason by type
        return sign_in_failed(request)

    user = await _db(users.user_for_identity, identity.subject, identity.email, identity.name)
    if isinstance(user, dict):
        if user.get("error") == "signups_closed":
            return message(request, "No new accounts today", user["message"], user["fix"], status_code=403)
        return message(request, "Sign-in failed", user["message"], user["fix"], status_code=400)

    request.session.clear()  # a fresh session after sign-in: nothing carried over from before
    request.session[USER] = user.id
    if identity.session_id:
        request.session[PROVIDER_SESSION] = identity.session_id
    request.session[SIGNED_IN_AT] = int(time.time())
    csrf.token(request)
    return redirect(links.page("today"))  # decision 1: a person starts on Today, not Settings


async def sign_out(request: Request) -> Response:
    provider_session = request.session.get(PROVIDER_SESSION)
    request.session.clear()
    if isinstance(provider_session, str) and provider_session:
        try:
            return redirect(web_auth.sign_out_url(provider_session))
        except Exception as exc:
            log.warning("Provider sign-out address unavailable: %s", type(exc).__name__)
    return redirect(links.page("home"))


async def settings_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    zone_error = None
    if request.method == "POST":
        form = await request.form()
        sent = str(form.get("time_zone") or "")
        # The one path that stores a zone, the same one Claude uses; it locks the user row first.
        result = await write(user, "update_settings", services.update_settings, user.id, {"time_zone": sent}, "web")
        if "too_many_writes" in codes(result):
            return too_many(request, result)
        if not failed(result):  # changed: [] (the same zone again) is a success, marked as no change
            mark = UNCHANGED if result.get("changed") == [] else ("saved", "time_zone")
            if form.get(ZONE_CARD_FIELD) == SETTINGS_CARDS["time_zone"]:  # the Time zone card's own form
                return redirect(settings_landing("time_zone", mark))
            # The automatic save (base.html), as it always was: back to `next`, the page the person was on
            # (decision 1), else Settings with no anchor. A page drawn with an error sends no `next`.
            back = local_path(form.get("next"))
            if back is not None and urlsplit(back).path != "/settings":
                return redirect(marked(get_settings().site_origin + back, *mark))
            return redirect(marked(links.page("settings"), *mark))
        # The service's own text is written for Claude; the page says it in its own words. A post that chose
        # nothing is told to choose.
        zone_error = card_error(result, ZONE_ERROR_WORDS, ZONE_ERROR_GENERAL)
        if not sent.strip() and result.get("error") in ZONE_ERROR_WORDS:
            zone_error = ZONE_ERROR_EMPTY
    return await settings_view(request, user, zone_error=zone_error, status_code=400 if zone_error else 200)


def page_path(name: str) -> str:
    """A named page's path for a link on a page: links.page's address without the site's origin."""
    return urlsplit(links.page(name)).path


def settings_landing(kind: str, mark: tuple[str, str]) -> str:
    """Where a Settings card's post lands: Settings with the post's mark, at that card's own anchor."""
    return marked(f"{links.page('settings')}#{SETTINGS_CARDS[kind]}", *mark)


def card_error(result: dict, words: dict[str, str], general: str) -> str:
    """The line a card with one field says for a refusal: by its code, else the card's general line. Never the
    service's message or fix."""
    return words.get(result.get("error"), general)


def target_errors(result: dict, sent: dict) -> tuple[dict[str, str], list[str]]:
    """{field: the page's own line} for a refused targets post, and the lines for the top of the form.
    update_settings names no field in a refusal. So each field the form sent goes through update_settings' own check
    for it (account.SETTABLE, the check the service ran on it): the fields it refuses, in the order sent, are the
    refusal's problems, in the order the service gives them. Each is placed by its code and that field, never by its
    message. When the two do not agree one for one, or a code has no line for its field, the general line goes at the
    top of the form."""
    problems = [e for e in result.get("errors") or [] if isinstance(e, dict)] or [result]
    refused = []
    for field, value in sent.items():
        check = account_checks.SETTABLE.get(field)
        _, err = check(value) if check is not None else (None, None)
        if err:
            refused.append((field, err.get("error")))
    if [code for _, code in refused] != [p.get("error") for p in problems] or \
            any((code, field) not in TARGET_ERROR_WORDS for field, code in refused):
        return {}, [TARGETS_ERROR_GENERAL]
    return {field: TARGET_ERROR_WORDS[(code, field)] for field, code in refused}, []


async def settings_view(request: Request, user: User, zone_error: str | None = None, switch_error: str | None = None,
                        targets_refused: dict | None = None, targets_loose: list | None = None,
                        typed: dict | None = None, add_back: bool | None = None, status_code: int = 200) -> Response:
    """Settings' six cards. A form sent back with its refusal keeps what was typed (the targets and the add-back box)
    and shows no saved line. Each target's value is the stored one through formatting.field."""
    groups, selected = zone_choices(user.time_zone)
    refused = bool(zone_error or switch_error or targets_refused or targets_loose)
    saved = request.query_params.get("saved")
    saved_card = SETTINGS_CARDS[saved] if saved in SETTINGS_SAVED and not refused else None
    stored = {f: getattr(user, f) for f in TARGET_FIELDS}
    shown = {f: (typed[f] if typed is not None else
                 ("" if stored[f] is None else formatting.field(stored[f], TARGET_QUANTITIES[f])))
             for f in TARGET_FIELDS}
    return page(request, "settings.html", status_code=status_code, user=user, cards=SETTINGS_CARDS,
                card_field=ZONE_CARD_FIELD,
                saved_card=saved_card, saved_words=SETTINGS_SAVED.get(saved) if saved_card else None,
                zone_error=zone_error, zone_groups=groups, selected_zone=selected,
                targets=shown, target_errors=targets_refused or {}, targets_loose=targets_loose or [],
                add_back=bool(user.add_back_exercise_kcal) if add_back is None else add_back,
                switch_error=switch_error,
                choices=[(value, CLAUDE_CAN_LABELS[value], CLAUDE_CAN_LINES[value]) for value in CLAUDE_CAN],
                foods_href=page_path("foods"))


async def claude_can_page(request: Request) -> Response:
    """POST: the switch for what Claude may do. Only the person sets it, here; Claude is never offered it."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    form = await request.form()
    result = await write(user, "set_claude_can", services.set_claude_can, user.id, form.get("claude_can"))
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        return await settings_view(request, user, status_code=400,
                                   switch_error=card_error(result, SWITCH_ERROR_WORDS, SWITCH_ERROR_GENERAL))
    # The switch already at that setting: changed is False, marked as no change.
    mark = UNCHANGED if result.get("changed") is False else ("saved", "claude_can")
    return redirect(settings_landing("claude_can", mark))


# ---------------------------------------------------------------- Today and a day

async def today_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    # Any mark on this address goes on to the day.
    address = links.page("day", date=users.today_for(user))
    for mark in DAY_MARKS:
        if mark in request.query_params:
            address = marked(address, mark, request.query_params[mark])
    return redirect(address)


def kind_with_unit(kind: str) -> str:
    """A measurement kind named for a choice: its word from measurements.kind_word, then its unit in brackets,
    "Resting heart rate (bpm)". A kind whose unit is its own word, such as steps, is named by the word alone."""
    word, unit = measurements.kind_word(kind), rules.MEASUREMENTS[kind][0]
    return word if unit.casefold() == word.casefold() else f"{word} ({unit})"


def measurement_kinds() -> list[tuple[str, str]]:
    """(kind, words) for the Add measurement fold-out's Kind, in the schema's order, each named by
    kind_with_unit."""
    return [(k, kind_with_unit(k)) for k in MEASUREMENT_KINDS]


# The quantity formatting.show takes for a measurement, by the unit rules.MEASUREMENTS gives its kind.
MEASUREMENT_QUANTITIES = {"cm": "cm", "h": "hours", "bpm": "bpm", "steps": "steps", "ml": "ml"}


def measurement_quantity(kind: str) -> str:
    """The quantity of a measurement kind's value: waist is cm, sleep_hours is hours."""
    return MEASUREMENT_QUANTITIES[rules.MEASUREMENTS[kind][0]]


async def day_page(request: Request, form_error: dict | None = None, status_code: int = 200,
                   typed: dict | None = None) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    on = parse_day(request.path_params["date"])
    if on is None:
        return not_found_page(request)
    result = await _db(services.get_log, user.id, on.isoformat(), 1)
    today = users.today_for(user)
    later: list[date] = []  # days after today that hold an entry (left there by a change of time zone)
    if (not failed(result) and on >= today) or codes(result) == {"date_in_future"}:
        # Undated, get_log also lists those days, so a link to one never 404s and the days link to each other.
        undated = await _db(services.get_log, user.id, None, 1)
        if not failed(undated):
            later = sorted(date.fromisoformat(d["date"]) for d in undated["days"] if d["date"] > today.isoformat())
            if failed(result):
                result = undated
    if failed(result):
        return not_found_page(request)
    day = next((d for d in result["days"] if d["date"] == on.isoformat()), None)
    if day is None:
        return not_found_page(request)
    # Prev and next only ever name a day that has a page: up to today, every day; after it, only the
    # later days that hold an entry.
    if on > today:
        prev_day = max([today] + [d for d in later if d < on])
    else:
        prev_day = on - timedelta(days=1) if on - timedelta(days=1) > date.min else None
    next_day = on + timedelta(days=1) if on < today else next((d for d in later if d > on), None)
    activities = await cardio_names()
    cardio = day.get("cardio") or []
    food = day.get("food")
    state = await _db(services.account_empty, user.id)
    return page(request, "day.html", status_code=status_code, day=day, on=on, today=today,
                # The glance block, on today's page only: "now" figures, which would mislead on a past day.
                glance=await glance_of(user.id) if on == today else None,
                prev_day=prev_day, next_day=next_day,
                strip=day_strip(request.query_params), form_error=form_error or {},
                kind_names=KIND_NAMES, meals=MEALS,
                source_words=SOURCE_WORDS, measurement_quantity=measurement_quantity,
                food=food, cardio=cardio,
                # The side cards: a measurement's name, the fold-outs' choices (each activity, each kind
                # with its unit), and whether any cardio row says No estimate, for the footnote.
                kind_word=measurements.kind_word, kind_choices=measurement_kinds(),
                activity_choices=[(name, name) for name in activities],
                lacks_estimate=any(not c.get("estimate") for c in cardio),
                # The Food card: each entry's lines, the estimates row's condition, the fold-out's choices,
                # what was typed into it before an error, and the marks that send it open (?add=food).
                food_name=food_name, food_note=food_note, serving_words=serving_words,
                has_estimate=any(e.get("source") == "claude_estimate"
                                 for meal in ((food or {}).get("meals") or {}).values() for e in meal),
                meal_choices=MEAL_CHOICES, typed=typed or {}, adding=request.query_params.getlist("add"),
                # The Today card's state: a new account (today's page, account_empty), or the day's
                # food, by whether any meal holds an entry (a day can total 0 kcal with food logged).
                new_account=not failed(state) and state["empty"] and on == today,
                has_food=bool(food) and any((food.get("meals") or {}).values()),
                shows_estimate=any(c.get("estimate") for c in cardio))


async def glance_of(user_id: int) -> dict | None:
    """What Today's glance block shows, and Progress' tiles, sheets and Body weight card: get_trends and
    get_days as they come back, the words for what bodyweight.now is and for a missing rate, the two sheets'
    charts, and the chart pair's two compact charts, the Eaten a day one with the calorie target's line when
    one is set. The web works out no figure. None when either read fails: the page then draws no glance block.

    Each read takes the person's date from the clock. Read across their midnight, the two can name two dates,
    so both are read once more. If they still differ, the page draws no glance block for that one render."""
    for _ in range(2):
        trends = await _db(services.get_trends, user_id)
        days = await _db(services.get_days, user_id)
        if failed(trends) or failed(days):
            return None
        if trends["today"] == days["today"]:
            break
    else:
        return None
    today = date.fromisoformat(trends["today"])
    series = (trends.get("bodyweight") or {}).get("series")
    target = (trends.get("calories") or {}).get("target_kcal")
    return {"trends": trends, "days": days, "basis": weight_now_label(trends), "rate_words": rate_note(trends),
            "weight_chart": charts.weight_sheet_svg(series, today),
            "eaten_chart": charts.eaten_sheet_svg(days["days"], today),
            "weight_small": charts.weight_small_svg(series, today),
            "eaten_small": charts.eaten_small_svg(days["days"], today, target)}


SIZE_WORDS = ("small", "medium", "large")
UNIT_WORDS = {"g", "kg", "mg", "ml", "l", "oz", "lb", "tbsp", "tsp", "pc", "pcs"}
IRREGULAR = {"child", "person", "man", "woman", "foot", "tooth", "mouse", "goose", "leaf", "loaf", "knife", "half",
             "fish", "sheep", "deer"}


def _plural(word: str) -> str | None:
    """The word with a plain -s, or None when -s would be wrong or a guess (units, -o, -y, -s and the like)."""
    w = word.casefold()
    if not w.isalpha() or len(w) < 2 or w in UNIT_WORDS or w in IRREGULAR or w in SIZE_WORDS:
        return None
    if w.endswith(("s", "x", "z", "ch", "sh", "y", "o", "f", "fe")):
        return None
    return word + "s"


def serving_text(servings, label, page: bool = False) -> str:
    """How much of a saved food was eaten, in the serving's own words (decision 14). Only "1 <word>" and
    "1 <size> <word>" are put in the plural, and only where a plain -s is right: 2 of "1 roti" is
    "2 rotis", of "1 medium apple" "2 medium apples". Anything else is counted: "2 × 100 g", "2 × 1 oz".
    One serving is the label itself; no label is the bare amount. page=True puts a no-break space between
    the amount and the plural words (section C)."""
    amount = formatting.show(servings, "servings")
    text = str(label or "").strip()
    if not text:
        return amount
    if float(servings) == 1:  # one serving is the label itself, whatever its shape
        return text
    found = re.fullmatch(r"1 (?:(small|medium|large) )?([^\s]+)", text, re.I)
    plural = _plural(found.group(2)) if found else None
    if plural is None:
        return f"{amount} × {text}"
    size = f"{found.group(1)} " if found.group(1) else ""
    return f"{amount}{NBSP if page else ' '}{size}{plural}"


def same_name(description: str | None, name: str | None) -> bool:
    """True when an entry's description only repeats its saved food's name, so the page shows it once."""
    return (description or "").strip().casefold() == (name or "").strip().casefold()


NBSP = " "


def food_name(entry: dict) -> str:
    """An entry's line one: the saved food's name, else the description, as written."""
    return entry.get("saved_food_name") or entry.get("description") or ""


def food_note(entry: dict) -> str | None:
    """A description that differs from its saved food's name, for its own grey line. None otherwise."""
    name, description = entry.get("saved_food_name"), entry.get("description")
    if not name or not description or same_name(description, name):
        return None
    return description


def serving_words(entry: dict) -> str:
    """How much was eaten, after the kcal on line two: a saved food's servings in its own words, a USDA
    food's grams. Servings whose saved food is gone (its link cleared) are counted:
    "3 servings", "1 serving". Nothing for a number stated or estimated."""
    if entry.get("saved_food_name") is not None and entry.get("servings"):
        return serving_text(entry["servings"], entry.get("serving_label"), page=True)
    if entry.get("servings"):
        amount = formatting.show(entry["servings"], "servings")
        return f"{amount}{NBSP}{'serving' if amount == '1' else 'servings'}"
    if entry.get("source") == "usda" and entry.get("grams"):
        return formatting.show(entry["grams"], "grams", page=True)
    return ""


async def cardio_names() -> list[str]:
    """The cardio activities, as list_exercises gives them."""
    result = await _db(services.list_exercises, "cardio")
    return [e["name"] for e in result.get("exercises", [])]


# The day page's strips after a write, by mark and value (section L, the strips table). A mark with no row
# here shows no strip: saved=unchanged and saved=time_zone only make the page calm, and so does any value
# the table does not name. deleted={kind}:{id} is the Undo strip, read by undo_target.
DAY_STRIPS = {("saved", "confirmed"): "Estimate confirmed.",
              **{("saved", kind): f"{words} saved." for kind, words in ENTRY_WORDS.items()},
              **{("restored", kind): f"{words} restored." for kind, words in ENTRY_WORDS.items()}}


def edit_strip(params, words: dict) -> dict | None:
    """After an edit (contract edit-history.md, decision 9), from a mark whose value is {kind}:{id}: "{Kind} saved."
    with Undo, whose form posts undo_edit to the restore address. After its undo: the plain line "{Kind} edit
    undone.", with no button. Only for a kind `words` names and an id plain_id takes."""
    for mark, said in (("saved", "saved."), ("restored", "edit undone.")):
        kind, _, entry_id = str(params.get(mark) or "").partition(":")
        if kind in words and plain_id(entry_id):
            line = {"words": f"{words[kind]} {said}", "undo": None}
            return {**line, "undo": (kind, entry_id), "fields": ("undo_edit",)} if mark == "saved" else line
    return None


def undo_sent(form) -> tuple:
    """What a strip's form asks of restore_entry beyond a restore, as its arguments after `by`: nothing, or
    undo_edit."""
    return (True,) if common.truthy(form.get("undo_edit")) else ()


def undo_mark(kind: str, result: dict, undo: tuple) -> tuple[str, str]:
    """The mark after restore_entry. A restore: restored. An edit's undo: restored, with the entry's id, which reads
    "{Kind} edit undone.". The same when the last edit was already undone (Undo pressed again from the saved page,
    by Back): nothing changed, and the line is true."""
    return "restored", (f"{kind}:{result['id']}" if undo else kind)


# The page when an undo (True) or the web's restore (False) would land on a day that holds another entry: what it
# would have done, and what the person can do next. Only the page the button was pressed on offers it again, by
# Back, and pressed there again it is refused again while the other entry holds the day. No link reaches that
# page, so neither says to try again: each names what a link does reach.
DAY_TAKEN_WORDS = {
    True: ("move the entry", "Edit or delete the other one first. Then edit this entry and set its date by hand."),
    False: ("bring the entry back", "The deleted one stays deleted and the other one stays. To change that day, "
                                    "edit the entry that is there."),
}


def changed_page(request: Request, what: str = "entry", where: str = "the day") -> Response:
    """An edit form older than the entry's last change (entry_changed): nothing was saved."""
    return message(request, "Not saved", f"This {what} changed since this page was drawn, so nothing was saved.",
                   f"Go back to {where} and open it again.", status_code=409)


def day_strip(params) -> dict | None:
    """The one strip a day page shows after a write: {"words", "undo"}, where undo is the (kind, id) its Undo
    form restores, or None. The Undo strip first, then an edit's (edit_strip), then deleted, saved and restored.
    The words come from the tables, never from the address."""
    undo = undo_target(params.get("deleted"))
    if undo is not None:
        return {"words": f"{ENTRY_WORDS[undo[0]]} deleted.", "undo": undo}
    edited = edit_strip(params, ENTRY_WORDS)
    if edited is not None:
        return edited
    for mark in ("deleted", "saved", "restored"):
        words = DAY_STRIPS.get((mark, params.get(mark)))
        if words is not None:
            return {"words": words, "undo": None}
    return None


def undo_target(value: object) -> tuple[str, str] | None:
    """`deleted=<kind>:<id>` after a delete: what the Undo form restores. Only a known kind and an id of
    ASCII digits, 1 or more, with no leading zero: "²", "١٢" and "５" are digits to str.isdigit, never an id."""
    if not isinstance(value, str) or ":" not in value:
        return None
    kind, _, entry_id = value.partition(":")
    if kind not in EDIT_FIELDS or not plain_id(entry_id):
        return None
    return kind, entry_id


def plain_id(text: str) -> bool:
    """An id as an address mark carries it: ASCII digits, 1 to 10 of them, with no leading zero."""
    return text.isascii() and text.isdigit() and len(text) <= 10 and not text.startswith("0")


def form_message(kind: str, field: str, measured: str | None = None) -> str:
    """What the page says beside a field the service refused, in its own words."""
    if field == "date":
        return "That date cannot be used: choose a day up to today, from the last two years, with nothing else on it."
    if field == "kind":
        return "Choose a kind from the list."
    if kind == "bodyweight":
        return "That weight was not saved: give it in kg, from 20 to 400."
    if kind == "lift_session":
        return "Those notes were not saved: keep them under 2,000 characters."
    if measured in rules.MEASUREMENTS:
        unit, lo, hi, whole = rules.MEASUREMENTS[measured]
        in_unit = "" if unit == measured else f" in {unit}"
        quantity = measurement_quantity(measured)
        lowest, highest = formatting.show(lo, quantity, unit=False), formatting.show(hi, quantity, unit=False)
        return (f"That value was not saved: give {measurements.kind_word(measured).lower()}{in_unit}, from {lowest} to "
                f"{highest}" + (", as a whole number." if whole else "."))
    return "That value was not saved: give a number in the unit shown."


def error_field(kind: str, err: dict) -> str:
    """Which field a refusal is about. The services' errors carry a code, and their range and number
    messages start with the field's name ("kcal 6000 is outside ..."); anything else goes to the kind's
    first field."""
    code = err.get("error")
    if code in DATE_ERRORS:
        return "date"
    if code == "invalid_kind" and kind == "measurement":
        return "kind"
    if code == "unknown_exercise" and kind == "cardio_entry":
        return "exercise"
    # A message about one entry of several starts "Entry 2: ..."; the field comes after that.
    words = re.sub(r"^[A-Z][a-z]+ \d+: ", "", str(err.get("message") or "")).split()
    first = words[0].strip("':,.").casefold() if words else ""
    if first in FIELDS_OF.get(kind, ()):
        return first
    return FIRST_FIELD[kind]


def field_errors(kind: str, result: dict, measured: str | None = None) -> dict:
    """{field: the page's own message}, from a refused write: every error, a combined one included."""
    found = [e for e in result.get("errors") or [] if isinstance(e, dict)] or [result]
    out = {}
    for err in found:
        field = error_field(kind, err)
        out[field] = FIELD_HELP.get((kind, field)) or form_message(kind, field, measured)
    return out


# The fields each side card's fold-out keeps as typed after its error (the Food fold-out's are in log_on_day).
TYPED_FIELDS = {"bodyweight": ("kg",), "measurement": ("kind", "value"),
                "cardio_entry": ("exercise", "minutes", "distance_km")}


async def log_on_day(request: Request, kind: str) -> Response:
    """POST from the day page: a weigh-in, a measurement, a food or a cardio entry for that day. Each fold-out's form
    posts to its own id, so a refusal comes back at the form; every redirect that answers it ends in POST_LANDS."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in") + POST_LANDS)
    on = parse_day(request.path_params["date"])
    if on is None:
        return not_found_page(request)
    form = await request.form()
    if kind == "bodyweight":
        result = await write(user, "log_bodyweight", services.log_bodyweight, user.id,
                             {"kg": str(form.get("kg") or ""), "date": on.isoformat()}, "web")
    elif kind == "measurement":
        result = await write(user, "log_measurement", services.log_measurement, user.id,
                             {"kind": str(form.get("kind") or ""), "value": str(form.get("value") or ""),
                              "date": on.isoformat()}, "web")
    elif kind == "food_entry":
        result = await write(user, "log_food", services.log_food, user.id,
                             {"entries": [food_from_form(form)], "date": on.isoformat()}, "web")
    else:
        result = await write(user, "log_cardio", services.log_cardio, user.id,
                             {**cardio_from_form(form), "date": on.isoformat()}, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        measured = str(form.get("kind") or "") if kind == "measurement" else None
        errors = field_errors(kind, result, measured)
        # What was typed stays in the Food fold-out, which the error sends open.
        typed = {kind: {k: str(form.get(k) or "") for k in ("description", "kcal", "meal")}} \
            if kind == "food_entry" else None
        # And in the cardio, weigh-in and measurement fold-outs, the same way.
        if kind in TYPED_FIELDS:
            typed = {kind: {k: str(form.get(k) or "") for k in TYPED_FIELDS[kind]}}
        shown = await day_page(request, form_error={kind: errors}, status_code=400, typed=typed)
        if shown.status_code == 404:  # a day that has no page (after today): say why, plainly
            return message(request, "Not saved", " ".join(errors.values()), status_code=400)
        return shown
    # A duplicate wrote nothing: it is marked, but never as saved.
    mark = UNCHANGED if result.get("duplicate") is True else ("saved", kind)
    return redirect(marked(links.page("day", date=on), *mark) + POST_LANDS)


async def log_weigh_in(request: Request) -> Response:
    return await log_on_day(request, "bodyweight")


def food_from_form(form) -> dict:
    """One food entry from the day page's form. No meal unless one was chosen: the server picks the slot
    from the person's clock (decision 3). No tag and no `estimated`: the server tags it you_said."""
    entry = {"description": str(form.get("description") or ""), "kcal": str(form.get("kcal") or "")}
    meal = str(form.get("meal") or "")
    if meal:
        entry["meal"] = meal
    return entry


def cardio_from_form(form) -> dict:
    """A cardio entry from the day page's form: activity, minutes, and the distance when one is given."""
    data = {"exercise": str(form.get("exercise") or ""), "minutes": str(form.get("minutes") or "")}
    distance = str(form.get("distance_km") or "").strip()
    if distance:
        data["distance_km"] = distance
    return data


async def log_measurement(request: Request) -> Response:
    return await log_on_day(request, "measurement")


async def log_food_page(request: Request) -> Response:
    return await log_on_day(request, "food_entry")


async def log_cardio_page(request: Request) -> Response:
    return await log_on_day(request, "cardio_entry")


# ---------------------------------------------------------------- one entry: edit, delete, restore

async def edit_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    kind, entry_id = request.path_params["kind"], request.path_params["entry_id"]
    if kind not in EDIT_FIELDS:
        return not_found_page(request)
    found = await _db(services.get_entry, user.id, kind, entry_id)
    if failed(found):
        return not_found_page(request)
    entry, errors, status = found["entry"], {}, 200
    values, drawn_at = edit_values(kind, entry), found["updated_at"]
    if request.method == "POST":
        form = await posted_form(request)
        drawn_at = str(form.get("updated_at") or "")  # as the page that sent this form drew it
        changes = edit_changes(kind, entry, form)
        if not changes:  # nothing differs: nothing to write
            return redirect(marked(links.page("day", date=entry["date"]), *UNCHANGED))
        result = await write(user, "edit_entry", services.edit_entry, user.id, kind, entry_id, changes, "web",
                             drawn_at)
        if "too_many_writes" in codes(result):
            return too_many(request, result)
        if "not_found" in codes(result):
            return not_found_page(request)
        if "entry_changed" in codes(result):
            return changed_page(request)
        if not failed(result) or codes(result) == {"nothing_to_change"}:
            after = result.get("after") or entry
            # An edit that changed the entry names it, so the strip offers Undo (edit_strip).
            saved = f"{kind}:{result['id']}" if result.get("changed") else kind
            mark = UNCHANGED if failed(result) else ("saved", saved)
            return redirect(marked(links.page("day", date=after["date"]), *mark))
        errors, status = field_errors(kind, result, entry.get("kind") if kind == "measurement" else None), 400
        values = {f: str(form.get(f) or "") for f in EDIT_FIELDS[kind]}  # what they typed, as typed
    activities = await cardio_names() if kind == "cardio_entry" else []
    # The day the entry is on, as stored: the back link and Cancel go there, whatever date was typed.
    on, today = date.fromisoformat(entry["date"]), users.today_for(user)
    estimate = entry.get("estimate") if kind == "cardio_entry" else None
    _, _, _, whole = rules.MEASUREMENTS[entry["kind"]] if kind == "measurement" else (None, None, None, False)
    return page(request, "edit.html", status_code=status, kind=kind, kind_name=KIND_NAMES[kind],
                entry_id=found["id"], entry=entry, values=values, errors=errors, updated_at=drawn_at,
                # A refusal about a field the form does not draw goes at the top of the form.
                loose=[words for field, words in errors.items() if field not in EDIT_FIELDS[kind]],
                day_href=f"/day/{on.isoformat()}",
                day_words="Today" if on == today else formatting.day(on, today),
                # A measurement's line above its Value, and a whole-number kind's numeric keyboard.
                kind_with_unit=kind_with_unit,
                value_inputmode="numeric" if whole else "decimal",
                meal_choices=MEAL_CHOICES,
                activity_choices=[(name, name) for name in activities],
                source_words=SOURCE_WORDS, estimate_kcal=estimate["kcal"] if estimate else None,
                shows_estimate=estimate is not None)


# The fields a page draws as a textarea: the edit pages' notes, the Goals form's text. A browser sends a textarea's
# line breaks as CRLF.
TEXTAREAS = frozenset({"notes", "text"})


def line_breaks(text: str) -> str:
    """Each line break as one newline: CRLF and a lone CR become LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


async def posted_form(request: Request) -> dict:
    """The posted form, each textarea's line breaks read as LF, once, before anything compares or saves it.
    So a note posted as drawn is the note it was, and no line break is stored as CRLF."""
    form = await request.form()
    return {key: line_breaks(value) if key in TEXTAREAS and isinstance(value, str) else value
            for key, value in form.items()}


def edit_values(kind: str, entry: dict) -> dict:
    """Each field's value in an edit form, as stored: a number at the decimals the store keeps, through
    formatting.field, other words as they are, nothing for a field with no value."""
    out = {}
    for field in EDIT_FIELDS[kind]:
        value = entry.get(field)
        if value is None:
            out[field] = ""
        elif field in NUMBER_FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool):
            quantity = measurement_quantity(entry["kind"]) if field == "value" else FIELD_QUANTITIES[field]
            out[field] = formatting.field(value, quantity)
        else:
            out[field] = str(value)
    return out


def changed_fields(fields, current: dict, form) -> dict:
    """Only the fields the person changed, as they typed them: the service checks and stores them. An
    optional number left empty where none is stored is no change; emptied where one is stored, it is
    sent empty, and the service decides."""
    changes = {}
    for field in fields:
        sent = str(form.get(field) or "")
        now = current.get(field)
        if field in NUMBER_FIELDS:
            if not sent.strip():
                same = now is None
            else:
                number = rules.to_float(sent)
                same = number is not None and now is not None and number == float(now)
        elif field == "notes":  # a note stored with CRLF is the same note posted back with LF
            same = sent.strip() == line_breaks(now or "").strip()
        else:
            same = sent.strip() == str(now if now is not None else "")
        if not same:
            changes[field] = sent
    return changes


def edit_changes(kind: str, entry: dict, form) -> dict:
    return changed_fields(EDIT_FIELDS[kind], entry, form)


async def confirm_page(request: Request) -> Response:
    """POST: the person confirms Claude's estimate for a food entry. "Yes, that's right" is the same
    number, stated (decision 1): the entry's own kcal goes to edit_entry explicitly, and the service
    makes it you_said. Back to the day."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    entry_id = request.path_params["entry_id"]
    found = await _db(services.get_entry, user.id, "food_entry", entry_id)
    if failed(found):
        return not_found_page(request)
    entry = found["entry"]
    if entry.get("source") != "claude_estimate":  # nothing to confirm: already the person's or the app's number
        return redirect(marked(links.page("day", date=entry["date"]), *UNCHANGED))
    result = await write(user, "edit_entry", services.edit_entry, user.id, "food_entry", entry_id,
                         {"kcal": entry["kcal"]}, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_found" in codes(result):
        return not_found_page(request)
    if failed(result):
        return message(request, "Not confirmed", "That estimate could not be confirmed, so nothing was changed.",
                       "Go back to the day and try again, or edit the entry.", status_code=400)
    return redirect(marked(links.page("day", date=(result.get("after") or entry)["date"]), "saved", "confirmed"))


async def delete_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    kind, entry_id = request.path_params["kind"], request.path_params["entry_id"]
    if kind not in EDIT_FIELDS:
        return not_found_page(request)
    result = await write(user, "delete_entry", services.delete_entry, user.id, kind, entry_id)
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        return not_found_page(request)
    return redirect(links.page("day", date=result["entry"]["date"]) + f"?deleted={kind}:{result['id']}")


async def restore_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    kind, entry_id = request.path_params["kind"], request.path_params["entry_id"]
    if kind not in EDIT_FIELDS:
        return not_found_page(request)
    undo = undo_sent(await request.form())
    result = await write(user, "restore_entry", services.restore_entry, user.id, kind, entry_id, "web", *undo)
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_deleted" in codes(result):
        return message(request, "Already back", "That entry is not deleted, so there was nothing to undo.",
                       "Go back to the day to see it.", status_code=409)
    if "nothing_to_undo" in codes(result):
        return message(request, "Nothing to undo", "That entry has no earlier values kept, so nothing was changed.",
                       "Go back to the day to see it.", status_code=409)
    if "day_taken" in codes(result):  # neither an undo nor the web's restore ever replaces another entry
        how, then = DAY_TAKEN_WORDS[bool(undo)]
        return message(request, "Nothing changed", f"That would {how} onto a day that has another one, so nothing "
                       "was changed.", then, status_code=409)
    if failed(result):
        return not_found_page(request)
    return redirect(marked(links.page("day", date=result["entry"]["date"]), *undo_mark(kind, result, undo)))


# ---------------------------------------------------------------- Foods

# The kind save_food, delete_entry and restore_entry name a saved food by, and its one spelling on the page.
SAVED_FOOD = "saved_food"
SAVED_FOOD_WORDS = "Saved food"
# Foods' strips after a write, by mark and value (section L, Foods). A mark with no row here shows no strip:
# saved=unchanged only makes the page calm, and so does any value the table does not name. deleted=saved_food:{id}
# is the Undo strip, read by food_strip.
FOOD_STRIPS = {("saved", SAVED_FOOD): f"{SAVED_FOOD_WORDS} saved.",
               ("restored", SAVED_FOOD): f"{SAVED_FOOD_WORDS} restored."}
# A refusal of a field the form does not draw, or of none, sits at the top of the form in these words.
FOOD_ERROR_GENERAL = "That food was not saved. Check each field and try again."
# The fields the edit page draws: the name stays, since it is how save_food finds the food.
FOOD_EDIT_FIELDS = FOOD_FIELDS[1:]


def food_form(form) -> dict:
    data = {f: str(form.get(f) or "") for f in FOOD_FIELDS}
    if not data["grams_per_serving"].strip():
        del data["grams_per_serving"]
    return data


def food_key(name: object) -> str:
    """A saved food's name as save_food matches it (its name_key): its words, one space apart, casefolded."""
    return " ".join(str(name or "").split()).casefold()


def same_food(before: dict | None, result: dict) -> bool:
    """Whether a save left the food as it was: the live food save_food answered with (by its id) held the same name,
    serving, kcal and grams before as save_food's own food after. save_food answers such a save as a success, so
    the web read the food first."""
    after = result.get("food") or {}
    return before is not None and all(before.get(f) == after.get(f) for f in FOOD_FIELDS)


def food_errors(result: dict, drawn) -> tuple[dict[str, str], list[str]]:
    """A refused save in the page's own words, never save_food's: {field: line} for each refused field the form
    draws (its message starts with the field's name, as save_food's checks write it), and the one general line at
    the top for any other refusal. Every problem of a combined refusal is placed."""
    errors, loose = {}, []
    for err in [e for e in result.get("errors") or [] if isinstance(e, dict)] or [result]:
        words = str(err.get("message") or "").split()
        field = words[0] if words else ""
        if field in drawn:
            errors[field] = FIELD_HELP[(SAVED_FOOD, field)]
        elif FOOD_ERROR_GENERAL not in loose:
            loose.append(FOOD_ERROR_GENERAL)
    return errors, loose


def food_values(food: dict) -> dict:
    """A saved food's edit fields as stored: the serving as written, the kcal and grams at the decimals the store
    keeps (formatting.field), nothing for no grams."""
    out = {"serving_label": str(food.get("serving_label") or "")}
    for field in ("kcal_per_serving", "grams_per_serving"):
        value = food.get(field)
        out[field] = "" if value is None else formatting.field(value, FIELD_QUANTITIES[field])
    return out


def food_strip(params) -> dict | None:
    """The one strip Foods shows after a write: {"words", "undo"}, where undo is the id of the food its Undo form
    restores, or None. The Undo strip first, then saved and restored. The words come from the table, never from the
    address."""
    kind, _, food_id = str(params.get("deleted") or "").partition(":")
    if kind == SAVED_FOOD and plain_id(food_id):
        return {"words": f"{SAVED_FOOD_WORDS} deleted.", "undo": food_id}
    edited = edit_strip(params, {SAVED_FOOD: SAVED_FOOD_WORDS})  # after a food's edit, and after its undo
    if edited is not None:
        return {**edited, "undo": edited["undo"] and edited["undo"][1]}
    for mark in ("saved", "restored"):
        words = FOOD_STRIPS.get((mark, params.get(mark)))
        if words is not None:
            return {"words": words, "undo": None}
    return None


async def live_foods(user: User) -> list[dict]:
    """The person's live saved foods, by name, with times logged: list_saved_foods (decision 15), the
    same numbers find_food gives. Deleted ones are left off the page."""
    result = await _db(services.list_saved_foods, user.id)
    return [f for f in result.get("foods", []) if f.get("deleted_at") is None]


async def foods_view(request: Request, user: User, errors: dict | None = None, loose: list | None = None,
                     typed: dict | None = None, status_code: int = 200) -> Response:
    """Foods: the Saved foods card and the Save a food fold-out. After a refusal, each line under its field or at
    the top (loose), what was typed kept, and the fold-out open. The strip only on a page after a write."""
    saved = await live_foods(user)
    return page(request, "foods.html", status_code=status_code, foods=saved, errors=errors or {}, loose=loose or [],
                typed=typed or {}, limits=rules.TEXT_LIMITS,
                strip=food_strip(request.query_params) if status_code == 200 else None)


async def foods_page(request: Request) -> Response:
    """GET: the saved foods. POST: save one; the same name updates it (save_food's rule). A save that would change
    nothing writes nothing, and one save_food finds unchanged is marked so: neither says a food was saved. The Save a
    food form posts to /foods#save-food, so a refusal comes back at the form. Every redirect that answers the post
    ends in POST_LANDS, so the strip is in view."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in") + (POST_LANDS if request.method == "POST" else ""))
    if request.method == "GET":
        return await foods_view(request, user)
    form = await request.form()
    data = food_form(form)
    live = await live_foods(user)
    known = next((f for f in live if food_key(f["name"]) == food_key(data["name"])), None)
    if known is not None and not changed_fields(FOOD_FIELDS, known, form):
        return redirect(marked(links.page("foods"), *UNCHANGED) + POST_LANDS)
    result = await write(user, "save_food", services.save_food, user.id, data, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        errors, loose = food_errors(result, FOOD_FIELDS)
        return await foods_view(request, user, errors=errors, loose=loose,
                                typed={f: str(form.get(f) or "") for f in FOOD_FIELDS}, status_code=400)
    before = next((f for f in live if f["id"] == result.get("id")), None)
    # A live food this save changed is named, so the strip offers Undo (edit_strip): a new one has nothing to undo.
    saved = f"{SAVED_FOOD}:{result['id']}" if before else SAVED_FOOD
    mark = UNCHANGED if same_food(before, result) else ("saved", saved)
    return redirect(marked(links.page("foods"), *mark) + POST_LANDS)


async def food_edit_page(request: Request) -> Response:
    """One saved food: change its serving, kcal or grams. The name is how save_food finds it, so it stays. An edit
    that changes nothing writes nothing and says nothing was saved."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    food_id = request.path_params["food_id"]
    # The food's updated_at, for the form's hidden field: list_saved_foods does not give it. It is read before the
    # values, so a save that lands between the two reads can only make the form older than what it shows, and the
    # worst that comes of it is a refusal the person did not need. A POST carries the stamp its page drew. A GET
    # that finds no stamp (the food was deleted, or brought back between the reads) is the 404: a form with no
    # stamp is never drawn by a GET. A POST that carries none and has a refused field draws its form again with
    # none, as an entry's does: a missing stamp runs the write as it always ran.
    posted = request.method == "POST"
    drawn_at = None if posted else (await _db(services.get_entry, user.id, SAVED_FOOD, food_id)).get("updated_at")
    food = next((f for f in await live_foods(user) if str(f["id"]) == food_id), None)
    if food is None or not (posted or drawn_at):
        return not_found_page(request)
    errors, loose, status, values = {}, [], 200, food_values(food)
    if posted:
        form = await request.form()
        drawn_at = str(form.get("updated_at") or "")  # as the page that sent this form drew it
        if not changed_fields(FOOD_EDIT_FIELDS, food, form):
            return redirect(marked(links.page("foods"), *UNCHANGED))
        saved = await write(user, "save_food", services.save_food, user.id, {**food_form(form), "name": food["name"]},
                            "web", drawn_at)
        if "too_many_writes" in codes(saved):
            return too_many(request, saved)
        if "entry_changed" in codes(saved):
            return changed_page(request, "food", "Foods")
        if not failed(saved):
            mark = UNCHANGED if same_food(food, saved) else ("saved", f"{SAVED_FOOD}:{saved['id']}")
            return redirect(marked(links.page("foods"), *mark))
        (errors, loose), status = food_errors(saved, FOOD_EDIT_FIELDS), 400
        values = {f: str(form.get(f) or "") for f in FOOD_EDIT_FIELDS}  # what they typed, as typed
    return page(request, "food_edit.html", status_code=status, food=food, values=values, errors=errors, loose=loose,
                limits=rules.TEXT_LIMITS, updated_at=drawn_at)


async def food_delete_page(request: Request) -> Response:
    """POST: delete a saved food (soft), back to Foods with Undo. Entries logged with it keep their kcal."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    result = await write(user, "delete_entry", services.delete_entry, user.id, SAVED_FOOD,
                         request.path_params["food_id"])
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        return not_found_page(request)
    return redirect(marked(links.page("foods"), "deleted", f"{SAVED_FOOD}:{result['id']}"))


async def food_restore_page(request: Request) -> Response:
    """POST: Undo after a delete, back to Foods, which says the food is restored. With undo_edit: Undo after an
    edit, as an entry's (restore_page)."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    undo = undo_sent(await request.form())
    result = await write(user, "restore_entry", services.restore_entry, user.id, SAVED_FOOD,
                         request.path_params["food_id"], "web", *undo)
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_deleted" in codes(result):
        return message(request, "Already back", "That food is not deleted, so there was nothing to undo.",
                       "Go back to Foods to see it.", status_code=409)
    if "nothing_to_undo" in codes(result):
        return message(request, "Nothing to undo", "That food has no earlier values kept, so nothing was changed.",
                       "Go back to Foods to see it.", status_code=409)
    if failed(result):
        return not_found_page(request)
    return redirect(marked(links.page("foods"), *undo_mark(SAVED_FOOD, result, undo)))


# ---------------------------------------------------------------- the targets on Settings

def targets_from_form(form, user: User) -> dict:
    """The targets and the add-back switch, only those that differ from what is stored."""
    data = changed_fields(TARGET_FIELDS, {f: getattr(user, f) for f in TARGET_FIELDS}, form)
    add_back = form.get("add_back_exercise_kcal") == "on"
    if add_back != bool(user.add_back_exercise_kcal):
        data["add_back_exercise_kcal"] = add_back
    return data


async def targets_page(request: Request) -> Response:
    """POST from Settings: the calorie target, weekly sessions, weekly cardio minutes and add-back."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    form = await request.form()
    data = targets_from_form(form, user)
    if not data:  # nothing differs: marked, but never as saved
        return redirect(settings_landing("targets", UNCHANGED))
    result = await write(user, "update_settings", services.update_settings, user.id, data, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        errors, loose = target_errors(result, data)
        typed = {f: str(form.get(f) or "") for f in TARGET_FIELDS}  # what they typed, as typed
        return await settings_view(request, user, targets_refused=errors, targets_loose=loose, typed=typed,
                                   add_back=form.get("add_back_exercise_kcal") == "on", status_code=400)
    # Every value already stored (changed: []): marked as no change, as the zone and the switch are.
    mark = UNCHANGED if result.get("changed") == [] else ("saved", "targets")
    return redirect(settings_landing("targets", mark))


# ---------------------------------------------------------------- Progress

BODY_WEIGHT_DAYS = 28  # Progress' Body weight chart: the last 4 weeks


def _trend(user_id: int, today: date | None = None) -> dict:
    """What /progress shows beyond the reads it shares with Today: each lift's best set, and for a lift never
    done above 0 kg its most reps in one set (lifts.history's best_set and reps_only, the numbers get_progress
    uses), and the body weight chart. bodyweight_chart takes the weigh-in and goal rows themselves, so this
    reads them. Nothing is written, and no figure is worked out here: the card's counts and callouts come
    from get_trends and get_days.

    today: the date the page's other reads name (get_trends' today), so the chart's 28 days are theirs.
    Without it, the person's own date."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return {"best_sets": {}, "reps_only": {}, "bodyweight": None}
        today = today or users.today_for(user)
        history = lifts.history(s, user.id, today)
        weighed = body.weigh_ins(s, user.id, today)
        goals = list(s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "open")))
        # The last 28 days, today included (section D): the same points as get_trends' series.
        since = today - timedelta(days=BODY_WEIGHT_DAYS - 1)
        return {"best_sets": history["best_set"], "reps_only": history["reps_only"],
                "bodyweight": progress_data.bodyweight_chart(weighed, goals, today, since)}


def _lift_points(lift: dict) -> dict:
    """get_progress's lift as the history shape lift_chart reads: {points: {lift: [(date, value)]}}."""
    return {"points": {lift["exercise"]: [(date.fromisoformat(p["date"]), p["estimated_max_kg"])
                                          for p in lift["estimated_max_over_time"]]}} if lift[
        "estimated_max_over_time"] else {"points": {}}


async def progress_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    result = await _db(services.get_progress, user.id)
    if failed(result):
        return not_found_page(request)
    # The tiles, their sheets and the Body weight card read what Today's glance block reads (glance_of), so the
    # three tiles and sheets they share say the same on both pages. None when a read fails: the page then
    # draws none of them. The Trends card reads the glance's get_trends, so the tiles and the table say one day's
    # figures. With no glance it reads get_trends itself, and draws whenever that read answers. The Cardio card
    # reads get_cardio's row for every activity; a failed read draws no card. get_cardio takes the person's date
    # from the clock, so read across their midnight it can count another 28 days than the page's: then it is read
    # once more, as glance_of does, and if it still names another day the card is not drawn for that one render.
    glance = await glance_of(user.id)
    today = date.fromisoformat(glance["trends"]["today"] if glance else result["today"])
    trend = await _db(_trend, user.id, today)
    trends = glance["trends"] if glance else await _db(services.get_trends, user.id)
    cardio = await _db(services.get_cardio, user.id)
    if not failed(cardio) and cardio["today"] != today.isoformat():
        cardio = await _db(services.get_cardio, user.id)
    return page(request, "progress.html", lifts=result["lifts"], best_sets=trend["best_sets"],
                reps_only=trend["reps_only"], glance=glance, bodyweight=trend["bodyweight"],
                chart=charts.bodyweight_svg(trend["bodyweight"], today=today), slug=links.slug, today=today,
                trends=None if failed(trends) else trends,
                activities=None if failed(cardio) or cardio["today"] != today.isoformat() else cardio["activities"],
                weight_now_label=weight_now_label, rate_note=rate_note)


def weight_now_label(trends: dict) -> str:
    """What bodyweight.now is: the 7-day average when a weigh-in falls in the last 7 days, else the
    latest weigh-in (get_trends' own rule)."""
    try:
        today = date.fromisoformat(trends["today"])
        dates = [date.fromisoformat(p["date"]) for p in (trends.get("bodyweight") or {}).get("series") or []]
    except (KeyError, TypeError, ValueError):
        return "now"
    recent = any(d >= today - timedelta(days=6) for d in dates)
    return "7-day average" if recent else "latest weigh-in"


def rate_note(trends: dict) -> str:
    """Why there is no weekly rate, in the service's words: its note."""
    note = str(trends.get("note") or "").strip()
    return note or "No weekly rate yet: it needs more weigh-ins."


async def lift_names() -> dict[str, str]:
    result = await _db(services.list_exercises, "lift")
    return {links.slug(e["name"]): e["name"] for e in result.get("exercises", [])}


def _reps_only(user_id: int, lift: str, today: date) -> dict | None:
    """One lift's entry in lifts.history's reps_only, read as _trend reads it: {reps, date} for a lift never done
    above 0 kg, else None. get_progress does not copy it. today: the date get_progress named, so both reads are
    of one day."""
    with db.new_session() as s:
        return lifts.history(s, user_id, today)["reps_only"].get(lift)


async def progress_lift_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    name = (await lift_names()).get(request.path_params["lift"])
    if name is None:
        return not_found_page(request)
    result = await _db(services.get_progress, user.id, name)
    if failed(result):
        return not_found_page(request)
    # The page's one date is get_progress' today. The reps_only read takes that date, so it never names another.
    lift = result["lift"]
    today = date.fromisoformat(result["today"])
    bare = await _db(_reps_only, user.id, lift["exercise"], today)
    chart = progress_data.lift_chart(_lift_points(lift), lift["goals"], lift["exercise"])
    return page(request, "progress_lift.html", lift=lift, chart=charts.chart_svg(chart, today=today), today=today,
                bare=bare, sessions=list(reversed(lift["estimated_max_over_time"])), best_widened=best_widened,
                best_labels=lifts.BEST_LABELS)


def best_widened(best: dict) -> bool:
    """Whether a best and the one it beat show widened, together: an estimated max pair that prints
    the same at the shown step (D5). A heaviest weight is as entered, and never widens."""
    previous = best.get("previous")
    return (best.get("kind") == "estimated_max" and previous is not None
            and formatting.show(best["value"], "estimated_max") == formatting.show(previous, "estimated_max"))


# ---------------------------------------------------------------- Goals

def clock(minutes) -> str:
    """Minutes as m:ss, the way a cardio time is said: 27.5 is 27:30. formatting.clock."""
    return formatting.clock(minutes)


# A goal's figures take the goal unit's quantity (section C of the stage 6 contract).
GOAL_QUANTITIES = {"bodyweight": "bodyweight_derived", "lift": "estimated_max", "cardio_best": "goal_time"}


def goal_quantity(goal: dict) -> str:
    """The quantity of a goal's start, now and plan figures: kg, cm, bpm or a cardio time."""
    if goal.get("kind") in GOAL_QUANTITIES:
        return GOAL_QUANTITIES[goal["kind"]]
    return "bpm" if goal.get("unit") == "bpm" else "cm"


# A goal card's words (section L, Goals), each keyed by goal_charts' own fields. What the now figure is, by the
# goal's kind and its now_basis: a cardio time names its distance, a measurement its kind's word, lowered.
NOW_WORDS = {
    ("bodyweight", "average_7d"): "now, the 7-day average",
    ("bodyweight", "latest_weigh_in"): "now, the latest weigh-in",
    ("lift", "best_28d"): "now, the best estimated max in 28 days",
    ("lift", "latest"): "now, the latest session's estimated max",
    ("cardio_best", "best_28d"): "now, the best {distance} time in 28 days",
    ("cardio_best", "latest"): "now, the latest {distance} time",
    ("measurement", "latest"): "now, the latest {measurement}",
}
# The heading of Against the plan line, by vs_plan and line_ended: the plan line today while it runs, the target
# once it has ended or has no length.
PLAN_HEADINGS = {
    ("ahead", False): "Ahead of the plan line today", ("ahead", True): "Ahead of the target",
    ("on the line", False): "On the plan line today", ("on the line", True): "At the target",
    ("behind", False): "Behind the plan line today", ("behind", True): "Behind the target",
}
# By projection: the tag for each status, and what it means. Not enough data means the goal's own note.
STATUS_TAGS = {"reached": "Reached", "on track": "On track", "behind": "Behind", "not enough data": "Not enough data"}
STATUS_MEANS = {"reached": "The target is reached.", "on track": "At this pace the target is reached by the date.",
                "behind": "At this pace the target is not reached by the date."}


def now_words(goal: dict) -> str | None:
    """What a goal's now figure is, in grey words after it: NOW_WORDS by its kind and now_basis. None with nothing
    logged (now_basis null). A cardio time's distance goes through show, a measurement's word is kind_word's."""
    words = NOW_WORDS.get((goal.get("kind"), goal.get("now_basis")))
    if words is None:
        return None
    distance = formatting.show(goal["distance_km"], "km", page=True) if goal.get("distance_km") is not None else ""
    kind = goal.get("measurement_kind")
    return words.format(distance=distance, measurement=measurements.kind_word(kind).lower() if kind else "")


def now_widened(goal: dict) -> bool:
    """Close-call rule 2 on the page: the now figure widens exactly when plan_line's does, by formatting.widen on
    goal_charts' unrounded latest and should_be_today, so the card's now and plan_line's print the same figure."""
    latest, should = goal.get("latest"), goal.get("should_be_today")
    return latest is not None and should is not None and formatting.widen([latest, should], goal_quantity(goal))


def days_words(goal: dict) -> str | None:
    """'3 days ahead', '1 day behind': days_ahead through show, the word from vs_plan. None when days_ahead is 0 or
    null: the box then shows the gap alone, in plan_line."""
    days = goal.get("days_ahead")
    if not days or goal.get("vs_plan") not in ("ahead", "behind"):
        return None
    return f"{formatting.show(abs(days), 'count')}{NBSP}{'day' if abs(days) == 1 else 'days'} {goal['vs_plan']}"


def goal_words(goal: dict) -> dict:
    """One open goal card's words and its now figure's widening, from goal_charts' own fields: the now words, the
    heading of Against the plan line, the days line, the By projection tag, its class and what it means. The page
    builds no comparison and never works out whether the line has ended: vs_plan and line_ended say both."""
    status = goal.get("status")
    return {"now": now_words(goal), "widened": now_widened(goal),
            "heading": PLAN_HEADINGS.get((goal.get("vs_plan"), bool(goal.get("line_ended")))),
            "days": days_words(goal), "tag": STATUS_TAGS.get(status, status),
            "tag_class": STATUS_CLASSES.get(status, ""),
            "means": goal.get("note") if status == "not enough data" else STATUS_MEANS.get(status)}


# The goal path chart's legend (section L, Goals): each item with the key the stylesheet draws for it, named only
# when the chart draws its mark. Actual is a line with two points or more, a ringed dot alone with one.
GOAL_LEGEND = (
    (('<line class="plan" ',), "key line plan", "Plan line"),
    (('<polyline class="series" ',), "key line", "Actual"),
    (('<circle class="dot is-latest" ', '<circle class="dot" '), "key dot-key", "Actual"),
    (('<line class="projection" ',), "key line projection", "Projection"),
    (('<line class="today-line" ',), "key line today", "Today"),
)


def goal_chart(goal: dict, today) -> dict:
    """One open goal's path chart and its legend. The chart is charts.goal_svg of goal_charts' own goal, unrounded,
    drawn on the page's one today. Its desc says plan_line word for word and never widens its own figures (charts.py).
    The legend names what the drawing holds, read from the drawing itself: the page decides nothing the chart does.
    No plan (a stand-in read): no chart, no legend."""
    svg = charts.goal_svg(goal, today)
    drawn, legend = str(svg), []
    for marks, key, words in GOAL_LEGEND:
        if any(m in drawn for m in marks) and words not in (w for _, w in legend):
            legend.append((key, words))
    return {"svg": svg, "legend": legend if drawn else []}


def goal_from_form(form) -> dict:
    """The new-goal form as set_goal's data: every field the person filled in, as typed. Empty fields
    are left out, so a body weight goal sends no exercise. The service checks everything."""
    data = {}
    for field in GOAL_FIELDS:
        value = str(form.get(field) or "").strip()
        if value:
            data[field] = value
    return data


def goal_kind(value: object) -> str:
    """The kind the Set a goal form shows: set_goal's word for it, from the address or the posted form. A kind the
    page does not know, or none, is Body weight."""
    return value if isinstance(value, str) and value in dict(GOAL_KINDS) else GOAL_KIND_FIRST


def goal_error_words(err: dict, kind: str, measured: str = "") -> str | None:
    """The page's own line for one set_goal refusal, found by its error code and its field, never by its message:
    the kind's own line where the line differs by kind, a measurement's line named for the one chosen, or its
    GOAL_ERROR_UNNAMED line when none is. None for a refusal the table does not know."""
    key = (err.get("error"), err.get("field"))
    words = GOAL_ERROR_WORDS.get(key)
    if isinstance(words, dict):
        words = words.get(kind)
    if words is None or "{" not in words:
        return words
    if measured not in rules.MEASUREMENTS:
        return GOAL_ERROR_UNNAMED.get(key)
    unit, lo, hi, whole = rules.MEASUREMENTS[measured]
    return words.format(what=measurements.kind_word(measured).lower(), unit=unit,
                        span=rules.span(lo, hi, "goal_target"), whole=", as a whole number" if whole else "")


def goal_errors(result: dict, kind: str, typed: dict) -> tuple[dict[str, str], list[str]]:
    """A set_goal refusal on the form of `kind`: ({field: its line}, [the lines at the top of the form]). Each
    problem goes under its own field, by the field it names, when the form draws that field (Where you are now is
    drawn for its own error). A problem about a field the form does not draw, such as the hidden kind, goes at the
    top, and so does one the table does not know, as the one general line."""
    found = [e for e in result.get("errors") or [] if isinstance(e, dict)] or [result]
    drawn = (*GOAL_KIND_FIELDS[kind], "start_value")
    under: dict[str, list[str]] = {}
    top: list[str] = []
    for err in found:
        words = goal_error_words(err, kind, typed.get("measurement_kind") or "")
        if words is None:
            top.append(GOAL_ERROR_GENERAL)
        elif err.get("field") in drawn:
            under.setdefault(err["field"], []).append(words)
        else:
            top.append(words)
    return {field: " ".join(dict.fromkeys(lines)) for field, lines in under.items()}, list(dict.fromkeys(top))


def goal_note_codes(warnings) -> list[str]:
    """The codes of a set_goal result's warnings, in GOAL_NOTES order, each once. A warning that a field
    "was ignored" (exercise, measurement_kind, distance_km) is "ignored"."""
    found = set()
    for text in warnings or []:
        text = str(text)
        code = next((c for c, start, _ in GOAL_NOTES if start and text.startswith(start)), None)
        if code is None and "was ignored" in text:
            code = "ignored"
        if code:
            found.add(code)
    return [c for c, _, _ in GOAL_NOTES if c in found]


def goal_notes_shown(value: object) -> list[str]:
    """`notes=a,b` after a save: the page's own text for each known code. Anything else shows nothing."""
    codes = str(value or "").split(",") if isinstance(value, str) and len(value) <= 64 else []
    return [GOAL_NOTE_TEXT[c] for c in dict.fromkeys(codes) if c in GOAL_NOTE_TEXT]


def saved_address(saved: str, result: dict) -> str:
    codes = goal_note_codes(result.get("warnings"))
    return links.page("goals") + f"?saved={saved}" + (f"&notes={','.join(codes)}" if codes else "")


async def exercise_names(kind: str) -> list[str]:
    result = await _db(services.list_exercises, kind)
    return [e["name"] for e in result.get("exercises", [])]


async def goal_reads(user_id: int) -> tuple[dict, dict]:
    """The Goals page's two reads: get_goals with the closed goals, and goal_charts for the open goals, unrounded,
    and the page's one date. Each takes the person's date from the clock, so read across their midnight the two can
    name two dates: both are read once more, as glance_of does. If they still differ, goal_charts' date is the page's
    for that one render: get_goals gives the page only its closed goals, whose labels, statuses and closing times
    carry nothing worked out from the date. Either read's failure comes back as it is."""
    for _ in range(2):
        listed = await _db(services.get_goals, user_id, True)
        charted = await _db(services.goal_charts, user_id)
        if failed(listed) or failed(charted) or listed.get("today") == charted.get("today"):
            break
    return listed, charted


async def goals_view(request: Request, user: User, kind: str = GOAL_KIND_FIRST, errors: dict | None = None,
                     loose: list | None = None, typed: dict | None = None, status_code: int = 200) -> Response:
    """The Goals page, its Set a goal form showing `kind`: after a refusal, with each line under its field or at
    the top (loose), and what was typed kept."""
    listed, charted = await goal_reads(user.id)
    if failed(listed) or failed(charted):
        return not_found_page(request)
    saved = GOAL_SAVED.get(request.query_params.get("saved") or "") if status_code == 200 else None
    notes = goal_notes_shown(request.query_params.get("notes")) if saved else []
    goals, today = charted.get("goals") or [], charted.get("today")
    return page(request, "goals.html", status_code=status_code, goals=goals, notes=notes,
                words={g["goal_id"]: goal_words(g) for g in goals},
                charts={g["goal_id"]: goal_chart(g, today) for g in goals},
                closed=listed.get("closed") or [], errors=errors or {}, loose=loose or [], typed=typed or {},
                saved=saved, kinds=GOAL_KINDS, kind=kind,
                shows_start="start_value" in (errors or {}) or bool((typed or {}).get("start_value")),
                lift_choices=[(n, n) for n in await exercise_names("lift")] if kind == "lift" else [],
                activity_choices=[(n, n) for n in await cardio_names()] if kind == "cardio_best" else [],
                measurement_choices=[(k, kind_with_unit(k)) for k in GOAL_MEASUREMENTS],
                goal_quantity=goal_quantity, closed_words=CLOSED_WORDS,
                today=today, zone=user.time_zone)


async def goals_page(request: Request) -> Response:
    """GET: every goal, the form showing the kind ?kind= names. POST: a new goal of the form's kind, through
    set_goal as Claude sets one. A refusal sends the page back with the posted kind and what was typed, at the
    form (its action ends in #set-goal). Every redirect that answers the post ends in GOAL_POST_LANDS."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in") + (GOAL_POST_LANDS if request.method == "POST" else ""))
    if request.method == "GET":
        return await goals_view(request, user, kind=goal_kind(request.query_params.get("kind")))
    form = await posted_form(request)  # the Notes' line breaks as LF
    result = await write(user, "set_goal", services.set_goal, user.id, goal_from_form(form), "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        typed = {f: str(form.get(f) or "") for f in GOAL_FIELDS}
        kind = goal_kind(typed["kind"])
        errors, loose = goal_errors(result, kind, typed)
        return await goals_view(request, user, kind=kind, errors=errors, loose=loose, typed=typed, status_code=400)
    return redirect(saved_address("goal", result) + GOAL_POST_LANDS)


async def goal_status_now(user_id: int, goal_id: str) -> str | None:
    """The status one goal of this person has now, read by its id through get_entry: one owned row, its id parsed as
    set_goal parses it. "open", "hit" or "dropped"; None for no goal of theirs by that id, or a failed read (the
    write then goes ahead, and set_goal answers it)."""
    found = await _db(services.get_entry, user_id, "goal", goal_id)
    if failed(found):
        return None
    return (found.get("entry") or {}).get("status")


async def goal_status_page(request: Request) -> Response:
    """POST from Goals: Hit, Drop or Reopen one goal, through set_goal with its id and the status."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    form = await request.form()
    status = str(form.get("status") or "")
    goal_id = request.path_params["goal_id"]
    # A goal set to the status it already has: nothing to change, so nothing is written and the page says nothing
    # was saved. set_goal answers such a post as a success, so the web reads the status first.
    if status and await goal_status_now(user.id, goal_id) == status:
        return redirect(marked(links.page("goals"), *UNCHANGED))
    result = await write(user, "set_goal", services.set_goal, user.id, {"goal_id": goal_id, "status": status}, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_found" in codes(result):
        return not_found_page(request)
    if failed(result):
        return message(request, "Not changed", "That goal was not changed.",
                       "Go back to Goals and use Hit, Drop or Reopen.", status_code=400)
    return redirect(saved_address(status, result))


# ---------------------------------------------------------------- Settings: export, delete

def export_zip(files: dict) -> bytes:
    """The CSV texts as one zip, built in memory. Each file keeps only its own name."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(PurePosixPath(str(name).replace("\\", "/")).name or "file.txt", text)
    return buffer.getvalue()


async def export_page(request: Request) -> Response:
    """POST from Settings: everything the person owns, as a zip of CSVs (decision 10). A read: nothing changes."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    result = await _db(services.export_data, user.id)
    if failed(result):
        return message(request, "Not exported", "Spotter could not put your data together this time.",
                       "Go back to Settings and try again.", status_code=500)
    name = f"spotter-export-{users.today_for(user).isoformat()}.zip"
    return Response(await run_in_threadpool(export_zip, result["files"]), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _delete_counts(user_id: int) -> list[tuple[str, int]]:
    """(what, how many) of everything Delete my account removes, deleted rows included. A read only."""
    with db.new_session() as s:
        return [(label, s.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)))
                for model, label in DELETE_COUNTS]


async def delete_account_page(request: Request) -> Response:
    """GET: what goes. POST (decision 11): the sign-in at the provider first, then every row here through
    delete_account, then the session. A provider failure deletes nothing; a failure here leaves the
    sign-in gone and the account still working, and a retry finishes it."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    if request.method == "GET":
        return page(request, "delete_account.html", counts=await _db(_delete_counts, user.id),
                    settings_href=page_path("settings"))
    try:
        await web_auth.delete_identity(user.subject)
    except Exception:  # web_auth has logged the reason by type
        return message(request, "Not deleted", "Spotter could not delete your sign-in, so nothing was deleted.",
                       "Try again in a few minutes.", status_code=502)
    try:
        result = await write(user, "delete_account", services.delete_account, user.id)
    except Exception as exc:  # the database or a bug: the sign-in is gone, the rows are not
        log.warning("Web delete_account failed after the sign-in was deleted: %s", type(exc).__name__)
        result = None
    if result is None or failed(result):
        return message(request, "Not finished", "Your sign-in is deleted, but your data here is not deleted yet.",
                       "Try again now from Settings: you are still signed in here, and the delete finishes where it "
                       "stopped. Do not sign out until this finishes.", status_code=500)
    request.session.clear()  # as sign-out does; there is no provider sign-out: the provider user is gone
    request.state.user = None
    return message(request, "Your account is deleted", "Everything you logged here is deleted, and so is your sign-in.",
                   "Signing in again starts a new, empty account.")


async def placeholder(request: Request) -> Response:
    """A page that is not built yet. Never repeats any part of the address."""
    if await current_user(request) is None:
        return redirect(links.page("sign_in"))
    return message(request, "Not built yet", "This page is not built yet. What you logged is saved.",
                   "Meanwhile, ask your assistant to read your numbers back.")


# ---------------------------------------------------------------- routes

def _off(reason: str):
    async def off(request: Request) -> Response:
        return not_set_up(request, reason)

    return off


# A refused targets or switch post leaves its own address in the bar. A GET of it goes to Settings at that card's
# anchor, which signs a signed-out visitor in first: (the address, its card's kind in SETTINGS_CARDS).
CARD_ADDRESSES = (("/settings/targets", "targets"), ("/settings/claude-can", "claude_can"))


def to_card(kind: str):
    async def back(request: Request) -> Response:
        return redirect(f"{links.page('settings')}#{SETTINGS_CARDS[kind]}")

    return back


# Every signed-in page: (path, endpoint, methods). Each one redirects a signed-out visitor to sign-in.
PAGES = [
    ("/settings", settings_page, ["GET", "POST"]),
    ("/settings/claude-can", claude_can_page, ["POST"]),
    ("/today", today_page, ["GET"]),
    ("/day/{date}", day_page, ["GET"]),
    ("/day/{date}/weigh-in", log_weigh_in, ["POST"]),
    ("/day/{date}/measurement", log_measurement, ["POST"]),
    ("/day/{date}/food", log_food_page, ["POST"]),
    ("/day/{date}/cardio", log_cardio_page, ["POST"]),
    ("/foods", foods_page, ["GET", "POST"]),
    ("/foods/{food_id}/edit", food_edit_page, ["GET", "POST"]),
    ("/foods/{food_id}/delete", food_delete_page, ["POST"]),
    ("/foods/{food_id}/restore", food_restore_page, ["POST"]),
    ("/settings/targets", targets_page, ["POST"]),
    ("/entries/{kind}/{entry_id}/edit", edit_page, ["GET", "POST"]),
    ("/entries/food_entry/{entry_id}/confirm", confirm_page, ["POST"]),
    ("/entries/{kind}/{entry_id}/delete", delete_page, ["POST"]),
    ("/entries/{kind}/{entry_id}/restore", restore_page, ["POST"]),
    ("/progress", progress_page, ["GET"]),
    ("/progress/{lift}", progress_lift_page, ["GET"]),
    ("/goals", goals_page, ["GET", "POST"]),
    ("/goals/{goal_id}/status", goal_status_page, ["POST"]),
    ("/settings/export", export_page, ["POST"]),
    ("/settings/delete", delete_account_page, ["GET", "POST"]),
]


def routes(settings: Settings) -> list[BaseRoute]:
    """The web routes. Each carries its own middleware, so /mcp and /.well-known never see the cookie."""
    reason = problem(settings)
    stack = [Middleware(SecurityHeaders, headers=headers_for(settings)), Middleware(PageErrors)]
    if reason is None:
        stack.append(Middleware(SessionMiddleware, secret_key=settings.session_key,
                                session_cookie=SESSION_COOKIE, max_age=SESSION_SECONDS,
                                same_site="lax", https_only=settings.site_origin.startswith("https://")))
        loads = PageLoads()  # this app's page-load count: one app a process, so one count
        table = [("/", home, ["GET"]),
                 ("/sign-in", sign_in, ["GET"]),
                 ("/auth/callback", callback, ["GET"]),
                 ("/sign-out", sign_out, ["POST"])]
        # The signed-in pages, behind the page limit.
        table += [(path, page_limited(endpoint, loads), methods) for path, endpoint, methods in PAGES]
        table += [(path, to_card(kind), ["GET"]) for path, kind in CARD_ADDRESSES]
        table += [(path, page_limited(placeholder, loads), ["GET"]) for path in PLACEHOLDERS.values()]
    else:
        off = _off(reason)
        table = [("/", home, ["GET"]),
                 ("/sign-in", off, ["GET"]),
                 ("/auth/callback", off, ["GET"]),
                 ("/sign-out", off, ["POST"])]
        table += [(path, off, methods) for path, _, methods in PAGES]
        table += [(path, off, ["GET"]) for path, _ in CARD_ADDRESSES]
        table += [(path, off, ["GET"]) for path in PLACEHOLDERS.values()]

    built: list[BaseRoute] = []
    for path, endpoint, methods in table:
        handler = endpoint
        if any(m not in csrf.SAFE_METHODS for m in methods):
            handler = csrf.protect(with_form_key(handler), refuse)
        # Outermost, so a database outage while refusing a form is the not-set-up page, not a 500.
        handler = _db_safe(handler)
        built.append(Route(path, handler, methods=methods, middleware=stack))
    built.append(Mount("/static", app=StaticFiles(directory=str(HERE / "static")), name="static",
                       middleware=[Middleware(SecurityHeaders, headers=headers_for(settings, static=True))]))
    return built
