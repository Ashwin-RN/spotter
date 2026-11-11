"""The web pages: the home page, sign-in, Today and every day, editing and deleting entries, Progress,
and Settings.

    /                              what Spotter is, the MCP address, how to connect it to Claude
    /sign-in                       sends the person to the provider (signed in: on to /today)
    /auth/callback                 checks `state`, finishes the sign-in, finds or creates the user, on to /today
    /sign-out                      POST: ends the session
    /today                         on to /day/<today in the person's zone>
    /day/{date}                    one day: sessions with sets and bests, the weigh-in, measurements;
                                   prev and next; a weigh-in form and a measurement form (POST to
                                   /day/{date}/weigh-in and /day/{date}/measurement)
    /entries/{kind}/{id}/edit      the fields the kind allows on the web (GET, POST)
    /entries/{kind}/{id}/delete    POST: delete, back to the day with "{Kind} deleted." and Undo
    /entries/{kind}/{id}/restore   POST: undo a delete, back to the day
    /progress                      one line per lift, the Trends card (get_trends), and the body weight
                                   chart with its smoothed line
    /progress/{lift}               one lift: its chart, bests and sessions
    /goals                         every goal with its numbers (get_goals); Hit, Drop and Reopen POST to
                                   /goals/{goal_id}/status; one form for a new goal of any kind (POST)
    /settings                      the account, the time zone, and the switch for what Claude may do
                                   (the switch POSTs to /settings/claude-can); export, sample data, delete
    /settings/sample               POST: sample data into an empty account, then Today
    /settings/sample/remove        POST: every sample row removed, then Today
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

Web sign-in fails closed: without the provider values, a session key or the site address, every page
but / says sign-in is not set up, no session is kept, and MCP is unaffected.

Database calls are synchronous, so they run in a worker thread, never on the event loop.
"""

import hashlib
import io
import json
import logging
import re
import secrets
import time
import zipfile
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote as url_quote, urlencode, urlsplit, urlunsplit

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.exc import InterfaceError, OperationalError
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

from app import calllog, charts, csrf, db, formatting, links, metrics, rules, services, users, web_auth
from app.config import Settings, get_settings
from app.models import (
    CLAUDE_CAN, MEASUREMENT_KINDS, Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, Measurement, SavedFood, User,
)
from app.services import body, lifts, measurements, progress as progress_data
from app.services.common import SAMPLE_NOTE

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


def _environment() -> Environment:
    env = Environment(loader=FileSystemLoader(str(HERE / "templates")), autoescape=True)
    env.filters.update(FILTERS)
    env.globals["style_href"] = style_href()
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
    ("saved_food", "name"): "Give the food a name, up to 128 characters.",
    ("saved_food", "serving_label"): "Say what one serving is, such as 1 roti, up to 64 characters.",
    ("saved_food", "kcal_per_serving"): "Give the kcal in one serving, from 0 to 5,000.",
    ("saved_food", "grams_per_serving"): "Give the grams in one serving, or leave it empty.",
    ("settings", "calorie_target_kcal"): "Give a daily target from 800 to 10,000 kcal, as a whole number, or leave it empty.",
    ("settings", "target_sessions_per_week"): "Give sessions a week from 0 to 14, or leave it empty.",
    ("settings", "target_cardio_minutes_per_week"): "Give cardio minutes a week from 0 to 3,000, or leave it empty.",
    ("settings", "add_back_exercise_kcal"): "That setting was not saved.",
}
FIRST_FIELD = {"bodyweight": "kg", "measurement": "value", "lift_session": "notes", "food_entry": "description",
               "cardio_entry": "minutes", "saved_food": "name", "settings": "calorie_target_kcal"}
FIELDS_OF = {**EDIT_FIELDS, "saved_food": FOOD_FIELDS, "settings": (*TARGET_FIELDS, "add_back_exercise_kcal")}


# What each setting of the switch lets Claude do, in one line each.
CLAUDE_CAN_LINES = {
    "read": "Read only: Claude can read what you have logged and change nothing.",
    "log": "Read and log: Claude can also log, edit and restore entries and set goals, but not delete.",
    "log_delete": "Read, log and delete: Claude can also delete entries. A deleted entry can always be restored.",
}
CLAUDE_CAN_LABELS = {"read": "Read", "log": "Log", "log_delete": "Log and delete"}

# Goals (stage 5, decisions 1 to 3). The form shows every field for every kind; the service decides.
GOAL_KINDS = (("lift", "A lift: an estimated max in kg"), ("bodyweight", "Body weight in kg"),
              ("measurement", "A measurement: a body size or resting heart rate"),
              ("cardio_best", "A cardio time for a distance"))
GOAL_MEASUREMENTS = ("waist", "hips", "chest", "arm", "thigh", "resting_hr")  # decision 2: states, not daily amounts
GOAL_FIELDS = ("kind", "exercise", "measurement_kind", "distance_km", "target_value", "target_date", "start_value",
               "text")
GOAL_FIELD_NAMES = re.compile(r"\b(" + "|".join(GOAL_FIELDS) + r")\b")
# Which field a set_goal refusal belongs beside, by its code, where the code says it.
GOAL_ERROR_FIELDS = {"unknown_exercise": "exercise", "wrong_direction": "target_value",
                     "target_is_start": "target_value", "invalid_date": "target_date",
                     "date_not_in_future": "target_date", "cannot_change_kind": "kind"}
GOAL_SAVED = {"goal": "Goal set.", "hit": "Goal marked as hit.", "dropped": "Goal dropped.", "open": "Goal reopened."}
# set_goal's warnings, said in the page's own words. A saved goal's warnings travel across the redirect as
# these codes (as the day page carries its undo): (code, how the warning starts, what the page says).
GOAL_NOTES = (
    ("duplicate", "There is already an open goal",
     "You already had an open goal like this one. Both are kept: drop the one you no longer want."),
    ("start", "start_value was ignored",
     "The goal starts from what you have logged, not from the number you typed for where you are now."),
    ("past_date", "Reopened, but its target date",
     "Its date has passed, so there is nothing to aim at: set a new goal with a later date."),
    ("ignored", None, "Fields this kind of goal does not use were left out."),
)
GOAL_NOTE_TEXT = {code: text for code, _, text in GOAL_NOTES}
CLOSED_WORDS = {"hit": "hit", "dropped": "dropped"}
STATUS_CLASSES = {"reached": "tag-good", "on track": "tag-good", "behind": "tag-behind"}

# What Delete my account says goes, by count. Deleted rows count: they go too.
DELETE_COUNTS = ((LiftSession, "Lift sessions"), (Bodyweight, "Weigh-ins"), (Measurement, "Measurements"),
                 (FoodEntry, "Food entries"), (CardioEntry, "Cardio entries"), (SavedFood, "Saved foods"),
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

ZONE_REFUSED = {"message": "That time zone was not recognised, so nothing was changed.",
                "fix": "Choose your time zone from the list."}


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


# Only these mean "the database is not there". Any other error is a bug and surfaces as a 500.
# The engine hides bound values, so a logged statement never carries a person's data.
DATABASE_DOWN = (OperationalError, InterfaceError, db.DatabaseNotConfigured)


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


def failed(result: dict) -> bool:
    return "error" in result or "errors" in result


def codes(result: dict) -> set[str]:
    """Every error code in a service result, a combined one included."""
    found = {result["error"]} if isinstance(result.get("error"), str) else set()
    return found | {e.get("error") for e in result.get("errors") or [] if isinstance(e, dict)}


def logged_write(user_id: int, function: str, write, *args):
    """Run a service write with its call-log row under `function`, the name MCP uses for the same write.
    The row is stored (in progress) before the service runs, so the write limit counts this call while
    it runs, and completed afterwards. Runs in the worker thread with the write. A crash completes the
    row as internal_error and is raised."""
    started_at, start = db.utcnow(), time.monotonic()
    row_id = None
    try:
        row_id = calllog.start(function, started_at, user_id)
    except Exception as exc:  # noqa: BLE001 - a lost log row never costs the person their write
        log.warning("Web %s: the call log could not be started: %s", function, type(exc).__name__)
    result, crashed = None, True
    try:
        result = write(*args)
        crashed = False
        return result
    finally:
        ms = int((time.monotonic() - start) * 1000)
        if crashed:
            ok, code, size = False, "internal_error", 0
        else:
            ok = not failed(result)
            code = None if ok else str(result.get("error") or "invalid")
            size = len(json.dumps(result, default=str).encode("utf-8"))
        try:
            calllog.finish(row_id, ms, ok, code, size)
        except Exception as exc:  # noqa: BLE001 - a lost log row never costs the person their write
            log.warning("Web %s: the call log could not be finished: %s", function, type(exc).__name__)


async def write(user: User, function: str, service, *args) -> dict:
    """A web write: its call-log row, the service, the row completed, in a worker thread."""
    return await _db(logged_write, user.id, function, service, *args)


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

    run.__name__ = endpoint.__name__
    return run


# ---------------------------------------------------------------- pages

async def home(request: Request) -> Response:
    await current_user(request)
    return page(request, "home.html")


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
    error = None
    if request.method == "POST":
        form = await request.form()
        # The one path that stores a zone, the same one Claude uses; it locks the user row first.
        result = await write(user, "update_settings", services.update_settings, user.id,
                             {"time_zone": str(form.get("time_zone") or "")}, "web")
        if "too_many_writes" in codes(result):
            return too_many(request, result)
        if not failed(result):  # changed: [] (the same zone again) is a success, marked as no change
            mark = UNCHANGED if result.get("changed") == [] else ("saved", "time_zone")
            # The automatic save carries `next`, the page the person was on: they stay there (decision 1).
            back = local_path(form.get("next"))
            if back is not None and urlsplit(back).path != "/settings":
                return redirect(marked(get_settings().site_origin + back, *mark))
            return redirect(marked(links.page("settings"), *mark))
        # The service's own text is written for Claude; the page says it in its own words.
        error = ZONE_REFUSED
    return await settings_view(request, user, error=error, status_code=400 if error else 200)


async def settings_view(request: Request, user: User, error: dict | None = None, switch_error: bool = False,
                        target_errors: dict | None = None, typed: dict | None = None,
                        status_code: int = 200) -> Response:
    state = await _db(services.sample_state, user.id)
    groups, selected = zone_choices(user.time_zone)
    saved = request.query_params.get("saved")
    targets = {f: getattr(user, f) for f in TARGET_FIELDS}
    shown = {f: (typed[f] if typed is not None else
                 ("" if targets[f] is None else formatting.field(targets[f], TARGET_QUANTITIES[f])))
             for f in TARGET_FIELDS}
    return page(request, "settings.html", status_code=status_code, user=user, error=error,
                saved=saved == "time_zone" and error is None, switch_saved=saved == "claude_can",
                targets_saved=saved == "targets" and not target_errors, target_errors=target_errors or {},
                targets=shown,
                switch_error=switch_error, zone_groups=groups, selected_zone=selected,
                choices=[(value, CLAUDE_CAN_LABELS[value], CLAUDE_CAN_LINES[value]) for value in CLAUDE_CAN],
                sample_rows=state["sample_rows"])


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
        return await settings_view(request, user, switch_error=True, status_code=400)
    # The switch already at that setting: changed is False, marked as no change.
    mark = UNCHANGED if result.get("changed") is False else ("saved", "claude_can")
    return redirect(marked(links.page("settings"), *mark))


# ---------------------------------------------------------------- Today and a day

async def today_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    # The marks of a write that redirected here (sample data added or removed) go on to the day.
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
    state = await _db(services.sample_state, user.id)
    return page(request, "day.html", status_code=status_code, day=day, on=on, today=today,
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
                # The Today card's state: a new account (today's page, sample_state empty), or the day's
                # food, by whether any meal holds an entry (a day can total 0 kcal with food logged).
                sample_rows=state["sample_rows"], new_account=state["empty"] and on == today,
                has_food=bool(food) and any((food.get("meals") or {}).values()),
                shows_estimate=any(c.get("estimate") for c in cardio))


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
# What sample data adds to a sample food's name when a deleted saved food of the person's holds it
# (sample._free_name). The sample tag says it already, so the Food card shows the mark once.
SAMPLE_SUFFIX = " (sample)"


def food_name(entry: dict) -> str:
    """An entry's line one: the saved food's name, else the description. A sample row's saved food name
    loses the "(sample)" sample data may have added: the sample tag beside it says so. A description is
    the person's words, so it is never cut."""
    name = entry.get("saved_food_name")
    if not name:
        return entry.get("description") or ""
    if entry.get("is_sample") and name.casefold().endswith(SAMPLE_SUFFIX):
        return name[:-len(SAMPLE_SUFFIX)]
    return name


def food_note(entry: dict) -> str | None:
    """A description that differs from its saved food's name, for its own grey line. None otherwise."""
    name, description = entry.get("saved_food_name"), entry.get("description")
    if not name or not description or same_name(description, name):
        return None
    return description


def serving_words(entry: dict) -> str:
    """How much was eaten, after the kcal on line two: a saved food's servings in its own words, a USDA
    food's grams. Servings whose saved food is gone (Remove sample data unlinks a sample food) are counted:
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
DAY_STRIPS = {("deleted", "sample"): "Sample data removed.", ("saved", "sample"): "Sample data added.",
              ("saved", "confirmed"): "Estimate confirmed.",
              **{("saved", kind): f"{words} saved." for kind, words in ENTRY_WORDS.items()},
              **{("restored", kind): f"{words} restored." for kind, words in ENTRY_WORDS.items()}}


def day_strip(params) -> dict | None:
    """The one strip a day page shows after a write: {"words", "undo"}, where undo is the (kind, id) its Undo
    form restores, or None. The Undo strip first, then deleted, saved and restored. The words come from the
    tables, never from the address."""
    undo = undo_target(params.get("deleted"))
    if undo is not None:
        return {"words": f"{ENTRY_WORDS[undo[0]]} deleted.", "undo": undo}
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
    if kind not in EDIT_FIELDS or not (entry_id.isascii() and entry_id.isdigit()) or len(entry_id) > 10 \
            or entry_id.startswith("0"):
        return None
    return kind, entry_id


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
    """POST from the day page: a weigh-in, a measurement, a food or a cardio entry for that day."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
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
    return redirect(marked(links.page("day", date=on), *mark))


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
    values = edit_values(kind, entry)
    if request.method == "POST":
        form = await request.form()
        changes = edit_changes(kind, entry, form)
        if not changes:  # nothing differs: nothing to write
            return redirect(marked(links.page("day", date=entry["date"]), *UNCHANGED))
        result = await write(user, "edit_entry", services.edit_entry, user.id, kind, entry_id, changes, "web")
        if "too_many_writes" in codes(result):
            return too_many(request, result)
        if "not_found" in codes(result):
            return not_found_page(request)
        if not failed(result) or codes(result) == {"nothing_to_change"}:
            after = result.get("after") or entry
            mark = UNCHANGED if failed(result) else ("saved", kind)
            return redirect(marked(links.page("day", date=after["date"]), *mark))
        errors, status = field_errors(kind, result, entry.get("kind") if kind == "measurement" else None), 400
        values = {f: str(form.get(f) or "") for f in EDIT_FIELDS[kind]}  # what they typed, as typed
    activities = await cardio_names() if kind == "cardio_entry" else []
    # The day the entry is on, as stored: the back link and Cancel go there, whatever date was typed.
    on, today = date.fromisoformat(entry["date"]), users.today_for(user)
    estimate = entry.get("estimate") if kind == "cardio_entry" else None
    return page(request, "edit.html", status_code=status, kind=kind, kind_name=KIND_NAMES[kind],
                entry_id=found["id"], entry=entry, values=values, errors=errors,
                # A refusal about a field the form does not draw goes at the top of the form.
                loose=[words for field, words in errors.items() if field not in EDIT_FIELDS[kind]],
                day_href=f"/day/{on.isoformat()}",
                day_words="Today" if on == today else formatting.day(on, today),
                kind_word=measurements.kind_word, meal_choices=MEAL_CHOICES,
                activity_choices=[(name, name) for name in activities],
                source_words=SOURCE_WORDS, estimate_kcal=estimate["kcal"] if estimate else None,
                shows_estimate=estimate is not None)


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
        elif field == "notes":
            same = sent.strip() == (now or "").strip()
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
    result = await write(user, "restore_entry", services.restore_entry, user.id, kind, entry_id)
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_deleted" in codes(result):
        return message(request, "Already back", "That entry is not deleted, so there was nothing to undo.",
                       "Go back to the day to see it.", status_code=409)
    if failed(result):
        return not_found_page(request)
    return redirect(marked(links.page("day", date=result["entry"]["date"]), "restored", kind))


# ---------------------------------------------------------------- Foods

def food_form(form) -> dict:
    data = {f: str(form.get(f) or "") for f in FOOD_FIELDS}
    if not data["grams_per_serving"].strip():
        del data["grams_per_serving"]
    return data


async def live_foods(user: User) -> list[dict]:
    """The person's live saved foods, by name, with times logged: list_saved_foods (decision 15), the
    same numbers find_food gives. Deleted ones are left off the page."""
    result = await _db(services.list_saved_foods, user.id)
    return [f for f in result.get("foods", []) if f.get("deleted_at") is None]


async def foods_view(request: Request, user: User, errors: dict | None = None, typed: dict | None = None,
                     status_code: int = 200) -> Response:
    saved = await live_foods(user)
    state = await _db(services.sample_state, user.id)
    deleted = request.query_params.get("deleted")
    return page(request, "foods.html", status_code=status_code, foods=saved, errors=errors or {},
                sample_rows=state["sample_rows"],
                typed=typed or {}, saved=request.query_params.get("saved") == "1",
                undo=deleted if isinstance(deleted, str) and deleted.isdigit() and len(deleted) <= 10 else None)


async def foods_page(request: Request) -> Response:
    """GET: the saved foods. POST: save one; the same name updates it (save_food's rule)."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    if request.method == "GET":
        return await foods_view(request, user)
    form = await request.form()
    result = await write(user, "save_food", services.save_food, user.id, food_form(form), "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        return await foods_view(request, user, errors=field_errors("saved_food", result), typed=food_form(form),
                                status_code=400)
    return redirect(links.page("foods") + "?saved=1")


async def food_edit_page(request: Request) -> Response:
    """One saved food: change its serving or kcal. The name is how save_food finds it, so it stays."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    food_id = request.path_params["food_id"]
    food = next((f for f in await live_foods(user) if str(f["id"]) == food_id), None)
    if food is None:
        return not_found_page(request)
    errors, status = {}, 200
    if request.method == "POST":
        form = await request.form()
        data = {**food_form(form), "name": food["name"]}
        if not changed_fields(FOOD_FIELDS[1:], food, form):
            return redirect(marked(links.page("foods"), *UNCHANGED))
        saved = await write(user, "save_food", services.save_food, user.id, data, "web")
        if "too_many_writes" in codes(saved):
            return too_many(request, saved)
        if not failed(saved):
            return redirect(links.page("foods") + "?saved=1")
        errors, status = field_errors("saved_food", saved), 400
        food = {**food, **{f: str(form.get(f) or "") for f in FOOD_FIELDS[1:]}}
    return page(request, "food_edit.html", status_code=status, food=food, errors=errors,
                field_quantities=FIELD_QUANTITIES)


async def food_delete_page(request: Request) -> Response:
    """POST: delete a saved food (soft), back to Foods with Undo. Entries logged with it keep their kcal."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    result = await write(user, "delete_entry", services.delete_entry, user.id, "saved_food",
                         request.path_params["food_id"])
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        return not_found_page(request)
    return redirect(links.page("foods") + f"?deleted={result['id']}")


async def food_restore_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    result = await write(user, "restore_entry", services.restore_entry, user.id, "saved_food",
                         request.path_params["food_id"])
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_deleted" in codes(result):
        return message(request, "Already back", "That food is not deleted, so there was nothing to undo.",
                       "Go back to Foods to see it.", status_code=409)
    if failed(result):
        return not_found_page(request)
    return redirect(links.page("foods") + "?saved=1")


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
        return redirect(marked(links.page("settings"), *UNCHANGED))
    result = await write(user, "update_settings", services.update_settings, user.id, data, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        typed = {f: str(form.get(f) or "") for f in TARGET_FIELDS}
        return await settings_view(request, user, target_errors=field_errors("settings", result), typed=typed,
                                   status_code=400)
    return redirect(links.page("settings") + "?saved=targets")


# ---------------------------------------------------------------- Progress

BODY_WEIGHT_DAYS = 28  # Progress' Body weight chart: the last 4 weeks


def _trend(user_id: int) -> dict:
    """What /progress shows beyond get_progress's rows: each lift's best set (lifts.history, the numbers
    get_progress uses) and the body weight chart. bodyweight_chart takes the weigh-in and goal rows
    themselves, so this reads them. Nothing is written.

    weigh_ins_28d counts the weigh-ins in the chart's 28 days, the card's words key on it. average_7d is
    the 7-day average ending today, None with no weigh-in in the last 7 days: get_trends' bodyweight.now
    when it is an average, so the card and the Trends card agree."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return {"best_sets": {}, "bodyweight": None, "weigh_ins": 0, "weigh_ins_28d": 0,
                    "average_7d": None, "latest_weigh_in": None}
        today = users.today_for(user)
        best_sets = lifts.history(s, user.id, today)["best_set"]
        weighed = body.weigh_ins(s, user.id, today)
        goals = list(s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "open")))
        # The last 28 days, today included (section D): the same points as get_trends' series.
        since = today - timedelta(days=BODY_WEIGHT_DAYS - 1)
        chart = progress_data.bodyweight_chart(weighed, goals, today, since)
        # A weigh-in after today counts as today's (metrics.on_or_before), so b.date >= since is the chart's rule.
        in_window = sum(1 for b in weighed if b.date >= since)
        average = metrics.bodyweight_average([(metrics.on_or_before(b.date, today), b.kg) for b in weighed], today)
        latest = weighed[-1] if weighed else None
        return {"best_sets": best_sets, "bodyweight": chart, "weigh_ins": len(weighed), "weigh_ins_28d": in_window,
                "average_7d": average,
                "latest_weigh_in": {"date": latest.date.isoformat(), "kg": latest.kg} if latest else None}


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
    trend = await _db(_trend, user.id)
    trends = await _db(services.get_trends, user.id)
    state = await _db(services.sample_state, user.id)
    today = date.fromisoformat(result["today"])
    return page(request, "progress.html", lifts=result["lifts"], best_sets=trend["best_sets"], trend=trend,
                chart=charts.bodyweight_svg(trend["bodyweight"], today=today), slug=links.slug, today=today,
                trends=None if failed(trends) else trends, sample_rows=state["sample_rows"],
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
    """Why there is no weekly rate, in the service's words: its note without the sample-data sentence."""
    note = str(trends.get("note") or "").replace(SAMPLE_NOTE, "").strip()
    return note or "No weekly rate yet: it needs more weigh-ins."


async def lift_names() -> dict[str, str]:
    result = await _db(services.list_exercises, "lift")
    return {links.slug(e["name"]): e["name"] for e in result.get("exercises", [])}


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
    lift = result["lift"]
    chart = progress_data.lift_chart(_lift_points(lift), lift["goals"], lift["exercise"])
    today = date.fromisoformat(result["today"])
    return page(request, "progress_lift.html", lift=lift, chart=charts.chart_svg(chart, today=today), today=today,
                sessions=list(reversed(lift["estimated_max_over_time"])), best_widened=best_widened)


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


def goal_from_form(form) -> dict:
    """The new-goal form as set_goal's data: every field the person filled in, as typed. Empty fields
    are left out, so a body weight goal sends no exercise. The service checks everything."""
    data = {}
    for field in GOAL_FIELDS:
        value = str(form.get(field) or "").strip()
        if value:
            data[field] = value
    return data


def goal_error_field(err: dict) -> str:
    """Which field a set_goal refusal belongs beside: the field the error names, else its code, else
    the first field its message names (the services' messages start with the field), else its fix.
    "form" when none says."""
    if err.get("field") in GOAL_FIELDS:
        return err["field"]
    if err.get("error") in GOAL_ERROR_FIELDS:
        return GOAL_ERROR_FIELDS[err["error"]]
    text = str(err.get("message") or "")
    found = GOAL_FIELD_NAMES.search(text)
    if found:
        return found.group(1)
    if any(text.casefold().startswith(kind.replace("_", " ") + " ") for kind in rules.MEASUREMENTS):
        return "measurement_kind"  # "steps cannot be a goal: ..."
    fix = str(err.get("fix") or "")
    if re.search(r"\bstart_value\b", fix):  # "nothing to start from": the fix names distance_km too
        return "start_value"
    found = GOAL_FIELD_NAMES.search(fix)
    return found.group(1) if found else "form"


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


def goal_errors(result: dict) -> dict[str, list[dict]]:
    """{field: [{message, fix}]}: each refusal from set_goal, beside the field it is about."""
    found = [e for e in result.get("errors") or [] if isinstance(e, dict)] or [result]
    out: dict[str, list[dict]] = {}
    for err in found:
        out.setdefault(goal_error_field(err), []).append({"message": err.get("message"), "fix": err.get("fix")})
    return out


async def exercise_names(kind: str) -> list[str]:
    result = await _db(services.list_exercises, kind)
    return [e["name"] for e in result.get("exercises", [])]


async def goals_view(request: Request, user: User, errors: dict | None = None, typed: dict | None = None,
                     status_code: int = 200) -> Response:
    result = await _db(services.get_goals, user.id, True)
    if failed(result):
        return not_found_page(request)
    state = await _db(services.sample_state, user.id)
    saved = GOAL_SAVED.get(request.query_params.get("saved") or "") if status_code == 200 else None
    notes = goal_notes_shown(request.query_params.get("notes")) if saved else []
    return page(request, "goals.html", status_code=status_code, goals=result.get("goals") or [], notes=notes,
                closed=result.get("closed") or [], errors=errors or {}, typed=typed or {}, saved=saved,
                kinds=GOAL_KINDS, lifts=await exercise_names("lift"), activities=await cardio_names(),
                measurement_choices=[(k, measurements.kind_word(k), rules.MEASUREMENTS[k][0]) for k in GOAL_MEASUREMENTS],
                goal_quantity=goal_quantity, status_class=lambda s: STATUS_CLASSES.get(s, ""),
                closed_words=CLOSED_WORDS, sample_rows=state["sample_rows"], today=result.get("today"),
                zone=user.time_zone)


async def goals_page(request: Request) -> Response:
    """GET: every goal. POST: a new goal of any kind, through set_goal as Claude sets one."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    if request.method == "GET":
        return await goals_view(request, user)
    form = await request.form()
    result = await write(user, "set_goal", services.set_goal, user.id, goal_from_form(form), "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        typed = {f: str(form.get(f) or "") for f in GOAL_FIELDS}
        return await goals_view(request, user, errors=goal_errors(result), typed=typed, status_code=400)
    return redirect(saved_address("goal", result))


async def goal_status_page(request: Request) -> Response:
    """POST from Goals: Hit, Drop or Reopen one goal, through set_goal with its id and the status."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    form = await request.form()
    status = str(form.get("status") or "")
    result = await write(user, "set_goal", services.set_goal, user.id,
                         {"goal_id": request.path_params["goal_id"], "status": status}, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_found" in codes(result):
        return not_found_page(request)
    if failed(result):
        return message(request, "Not changed", "That goal was not changed.",
                       "Go back to Goals and use Hit, Drop or Reopen.", status_code=400)
    return redirect(saved_address(status, result))


# ---------------------------------------------------------------- Settings: sample data, export, delete

async def sample_add_page(request: Request) -> Response:
    """POST from an empty Today: four weeks of sample rows, every one marked, then back to Today."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    result = await write(user, "add_sample_data", services.add_sample_data, user.id, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "already_sample" in codes(result):
        return message(request, "Already added", "Sample data is already on this account.",
                       "Remove it on Settings to start again.", status_code=409)
    if "not_empty" in codes(result):
        return message(request, "Not added", "Sample data goes only into an empty account, and this one has "
                       "entries, saved foods or goals.", "Delete them on the day pages first, or keep your own.",
                       status_code=409)
    if failed(result):
        return message(request, "Not added", "Sample data could not be added, so nothing was changed.",
                       "Go back to Today and try again.", status_code=400)
    return redirect(marked(links.page("today"), "saved", "sample"))


async def sample_remove_page(request: Request) -> Response:
    """POST from Settings: every sample row goes, live or deleted. Real entries stay."""
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    result = await write(user, "remove_sample_data", services.remove_sample_data, user.id, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        return message(request, "Not removed", "The sample data could not be removed, so nothing was changed.",
                       "Go back to Settings and try again.", status_code=400)
    return redirect(marked(links.page("today"), "deleted", "sample"))


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
        return page(request, "delete_account.html", counts=await _db(_delete_counts, user.id))
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
    ("/settings/sample", sample_add_page, ["POST"]),
    ("/settings/sample/remove", sample_remove_page, ["POST"]),
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
        table = [("/", home, ["GET"]),
                 ("/sign-in", sign_in, ["GET"]),
                 ("/auth/callback", callback, ["GET"]),
                 ("/sign-out", sign_out, ["POST"]),
                 *PAGES]
        table += [(path, placeholder, ["GET"]) for path in PLACEHOLDERS.values()]
    else:
        off = _off(reason)
        table = [("/", home, ["GET"]),
                 ("/sign-in", off, ["GET"]),
                 ("/auth/callback", off, ["GET"]),
                 ("/sign-out", off, ["POST"])]
        table += [(path, off, methods) for path, _, methods in PAGES]
        table += [(path, off, ["GET"]) for path in PLACEHOLDERS.values()]

    built: list[BaseRoute] = []
    for path, endpoint, methods in table:
        handler = endpoint
        if any(m not in csrf.SAFE_METHODS for m in methods):
            handler = csrf.protect(handler, refuse)
        # Outermost, so a database outage while refusing a form is the not-set-up page, not a 500.
        handler = _db_safe(handler)
        built.append(Route(path, handler, methods=methods, middleware=stack))
    built.append(Mount("/static", app=StaticFiles(directory=str(HERE / "static")), name="static",
                       middleware=[Middleware(SecurityHeaders, headers=headers_for(settings, static=True))]))
    return built
