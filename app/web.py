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
    /entries/{kind}/{id}/delete    POST: delete, back to the day with "Deleted. Undo"
    /entries/{kind}/{id}/restore   POST: undo a delete, back to the day
    /progress                      one line per lift, and the body weight trend with its chart
    /progress/{lift}               one lift: its chart, bests and sessions
    /settings                      the account, the time zone, and the switch for what Claude may do
                                   (the switch POSTs to /settings/claude-can)
    PLACEHOLDERS                   every other page links.py names, until its real page is built

Every page but / and /sign-in needs sign-in. Every web write calls the service Claude's function calls,
then writes a call-log row under the same function name (rule 9 of stage 3), so the write limit counts
web and Claude together. The services' error texts are written for Claude; the pages say what went
wrong in their own words.

The session is Spotter's own signed cookie. After sign-in it holds exactly the user id, the CSRF token,
the provider's session id (for signing out there too) and the sign-in time. No provider token ever
goes into it. A session older than SESSION_DAYS from its sign-in is signed out, however recently the
cookie was renewed. Every non-GET route checks the CSRF token first.

Web sign-in fails closed: without the provider values, a session key or the site address, every page
but / says sign-in is not set up, no session is kept, and MCP is unaffected.

Database calls are synchronous, so they run in a worker thread, never on the event loop.
"""

import json
import logging
import re
import secrets
import time
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote as url_quote, urlsplit

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sqlalchemy import func, select

from app import calllog, charts, csrf, db, links, rules, services, users, web_auth
from app.config import Settings, get_settings
from app.models import CLAUDE_CAN, MEASUREMENT_KINDS, FoodEntry, Goal, SavedFood, User
from app.services import body, lifts, progress as progress_data

log = logging.getLogger("spotter")

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(env=Environment(loader=FileSystemLoader(str(HERE / "templates")), autoescape=True))

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
PLACEHOLDERS = {
    "goals": "/goals",
}

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
KIND_NAMES = {"bodyweight": "weigh-in", "measurement": "measurement", "lift_session": "lift session",
              "food_entry": "food", "cardio_entry": "cardio"}
NUMBER_FIELDS = {"kg", "value", "kcal", "minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr",
                 "machine_kcal", "kcal_per_serving", "grams_per_serving", "calorie_target_kcal",
                 "target_sessions_per_week", "target_cardio_minutes_per_week"}
DATE_ERRORS = {"invalid_date", "date_in_future", "date_too_old", "day_taken"}

MEALS = ("breakfast", "lunch", "dinner", "snack")
# Who supplied a food entry's number (rule 13), in words. An estimate is marked on the page.
SOURCE_WORDS = {"you_said": "you said", "saved_food": "saved food", "usda": "USDA", "claude_estimate": "estimate"}
ATTRIBUTION = ("Calorie estimates use MET values from the 2024 Adult Compendium of Physical Activities "
               "(CC BY-NC-ND 4.0).")
TARGET_FIELDS = ("calorie_target_kcal", "target_sessions_per_week", "target_cardio_minutes_per_week")
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

# Measurement kinds as the page names them. Units and ranges come from rules.MEASUREMENTS, the table the
# services check against.
MEASUREMENT_LABELS = {"sleep_hours": "Sleep", "resting_hr": "Resting heart rate", "water_ml": "Water"}

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
    """The fixed headers for web responses; HSTS only when the site is served over https."""
    values = dict(PAGE_HEADERS)
    if static:
        del values["Cache-Control"]
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


def page(request: Request, name: str, status_code: int = 200, **context) -> Response:
    settings = get_settings()
    user = getattr(request.state, "user", None)
    values = {
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
        **context,
    }
    return templates.TemplateResponse(request, name, values, status_code=status_code)


def message(request: Request, title: str, text: str, fix: str | None = None, status_code: int = 200) -> Response:
    return page(request, "message.html", status_code=status_code, title=title, message=text, fix=fix)


def redirect(address: str) -> Response:
    return RedirectResponse(address, status_code=303)


def sign_in_failed(request: Request) -> Response:
    return message(request, "Sign-in failed", "Spotter could not finish signing you in.",
                   "Try again from the sign-in page.", status_code=400)


def not_set_up(request: Request, reason: str | None = None) -> Response:
    return message(request, "Not set up yet", "Sign-in on the web is not set up on this server yet.",
                   reason or "The server needs its settings completed. MCP is unaffected.", status_code=503)


def refuse(request: Request) -> Response:
    return message(request, "Form expired", "This form could not be checked, so nothing was changed.",
                   "Go back, reload the page and try again.", status_code=403)


def not_found_page(request: Request) -> Response:
    """The one 404: it never says whether the thing exists for someone else, or repeats the address."""
    return message(request, "Not found", "There is nothing here.", "Go back to Today and start from there.",
                   status_code=404)


def too_many(request: Request, result: dict) -> Response:
    wait = result.get("wait_minutes")
    when = f"in {wait} minute{'s' if wait != 1 else ''}" if isinstance(wait, int) and wait > 0 else "in a few minutes"
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
        if not failed(result):  # changed: [] (the same zone again) is a success
            # The automatic save carries `next`, the page the person was on: they stay there (decision 1).
            back = local_path(form.get("next"))
            if back is not None and urlsplit(back).path != "/settings":
                return redirect(get_settings().site_origin + back)
            return redirect(links.page("settings") + "?saved=time_zone")
        # The service's own text is written for Claude; the page says it in its own words.
        error = ZONE_REFUSED
    return settings_view(request, user, error=error, status_code=400 if error else 200)


def settings_view(request: Request, user: User, error: dict | None = None, switch_error: bool = False,
                  target_errors: dict | None = None, typed: dict | None = None, status_code: int = 200) -> Response:
    groups, selected = zone_choices(user.time_zone)
    saved = request.query_params.get("saved")
    targets = {f: getattr(user, f) for f in TARGET_FIELDS}
    shown = {f: (typed[f] if typed is not None else ("" if targets[f] is None else rules.kg(targets[f])))
             for f in TARGET_FIELDS}
    return page(request, "settings.html", status_code=status_code, user=user, error=error,
                saved=saved == "time_zone" and error is None, switch_saved=saved == "claude_can",
                targets_saved=saved == "targets" and not target_errors, target_errors=target_errors or {},
                targets=shown,
                switch_error=switch_error, zone_groups=groups, selected_zone=selected,
                choices=[(value, CLAUDE_CAN_LABELS[value], CLAUDE_CAN_LINES[value]) for value in CLAUDE_CAN])


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
        return settings_view(request, user, switch_error=True, status_code=400)
    return redirect(links.page("settings") + "?saved=claude_can")


# ---------------------------------------------------------------- Today and a day

async def today_page(request: Request) -> Response:
    user = await current_user(request)
    if user is None:
        return redirect(links.page("sign_in"))
    return redirect(links.page("day", date=users.today_for(user)))


def measurement_kinds() -> list[tuple[str, str, str]]:
    """(kind, label, unit) for the measurement form, in the schema's order."""
    return [(k, measurement_label(k), rules.MEASUREMENTS[k][0]) for k in MEASUREMENT_KINDS]


def measurement_label(kind: str) -> str:
    return MEASUREMENT_LABELS.get(kind, kind.replace("_", " ").capitalize())


async def day_page(request: Request, form_error: dict | None = None, status_code: int = 200) -> Response:
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
    undo = undo_target(request.query_params.get("deleted"))
    # Prev and next only ever name a day that has a page: up to today, every day; after it, only the
    # later days that hold an entry.
    if on > today:
        prev_day = max([today] + [d for d in later if d < on])
    else:
        prev_day = on - timedelta(days=1) if on - timedelta(days=1) > date.min else None
    next_day = on + timedelta(days=1) if on < today else next((d for d in later if d > on), None)
    activities = await cardio_names()
    cardio = day.get("cardio") or []
    return page(request, "day.html", status_code=status_code, day=day, on=on, today=today,
                prev_day=prev_day, next_day=next_day,
                kinds=measurement_kinds(), undo=undo, form_error=form_error or {}, kg=rules.kg,
                kind_names=KIND_NAMES, labels=MEASUREMENT_LABELS, meals=MEALS, source_words=SOURCE_WORDS,
                food=day.get("food"), cardio=cardio, activities=activities,
                shows_estimate=any(c.get("estimate") for c in cardio))


async def cardio_names() -> list[str]:
    """The cardio activities, as list_exercises gives them."""
    result = await _db(services.list_exercises, "cardio")
    return [e["name"] for e in result.get("exercises", [])]


def undo_target(value: object) -> tuple[str, str] | None:
    """`deleted=<kind>:<id>` after a delete: what the Undo form restores. Only a known kind and digits."""
    if not isinstance(value, str) or ":" not in value:
        return None
    kind, _, entry_id = value.partition(":")
    if kind not in EDIT_FIELDS or not entry_id.isdigit() or len(entry_id) > 10:
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
        return (f"That value was not saved: give {measurement_label(measured).lower()}{in_unit}, from {lo:,} to "
                f"{hi:,}" + (", as a whole number." if whole else "."))
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


async def log_on_day(request: Request, kind: str) -> Response:
    """POST from the day page: a weigh-in or a measurement for that day."""
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
        shown = await day_page(request, form_error={kind: errors}, status_code=400)
        if shown.status_code == 404:  # a day that has no page (after today): say why, plainly
            return message(request, "Not saved", " ".join(errors.values()), status_code=400)
        return shown
    return redirect(links.page("day", date=on))


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
    if request.method == "POST":
        form = await request.form()
        changes = edit_changes(kind, entry, form)
        if not changes:  # nothing differs: nothing to write
            return redirect(links.page("day", date=entry["date"]))
        result = await write(user, "edit_entry", services.edit_entry, user.id, kind, entry_id, changes, "web")
        if "too_many_writes" in codes(result):
            return too_many(request, result)
        if "not_found" in codes(result):
            return not_found_page(request)
        if not failed(result) or codes(result) == {"nothing_to_change"}:
            after = result.get("after") or entry
            return redirect(links.page("day", date=after["date"]))
        errors, status = field_errors(kind, result, entry.get("kind") if kind == "measurement" else None), 400
        entry = {**entry, **{f: str(form.get(f) or "") for f in EDIT_FIELDS[kind]}}  # show what they typed
    activities = await cardio_names() if kind == "cardio_entry" else []
    return page(request, "edit.html", status_code=status, kind=kind, kind_name=KIND_NAMES[kind],
                entry_id=found["id"], entry=entry, errors=errors, kg=rules.kg, labels=MEASUREMENT_LABELS,
                meals=MEALS, source_words=SOURCE_WORDS, activities=activities,
                shows_estimate=kind == "cardio_entry" and entry.get("estimated_kcal") is not None)


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
        return redirect(links.page("day", date=entry["date"]))
    result = await write(user, "edit_entry", services.edit_entry, user.id, "food_entry", entry_id,
                         {"kcal": entry["kcal"]}, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if "not_found" in codes(result):
        return not_found_page(request)
    if failed(result):
        return message(request, "Not confirmed", "That estimate could not be confirmed, so nothing was changed.",
                       "Go back to the day and try again, or edit the entry.", status_code=400)
    return redirect(links.page("day", date=(result.get("after") or entry)["date"]))


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
    return redirect(links.page("day", date=result["entry"]["date"]))


# ---------------------------------------------------------------- Foods

def food_form(form) -> dict:
    data = {f: str(form.get(f) or "") for f in FOOD_FIELDS}
    if not data["grams_per_serving"].strip():
        del data["grams_per_serving"]
    return data


def _saved_foods(user_id: int) -> list[dict]:
    """Every live saved food of this person with how often it was logged, by name. A read only.
    No service lists them: find_food needs a query, stops at 25 and asks USDA when the key is set."""
    with db.new_session() as s:
        used = dict(s.execute(select(FoodEntry.saved_food_id, func.count()).where(
            FoodEntry.user_id == user_id, FoodEntry.deleted_at.is_(None), FoodEntry.saved_food_id.is_not(None))
            .group_by(FoodEntry.saved_food_id)).all())
        foods = s.scalars(select(SavedFood).where(SavedFood.user_id == user_id, SavedFood.deleted_at.is_(None))
                          .order_by(SavedFood.name_key))
        return [{"id": f.id, "name": f.name, "serving_label": f.serving_label, "kcal_per_serving": f.kcal_per_serving,
                 "grams_per_serving": f.grams_per_serving, "times_logged": used.get(f.id, 0)} for f in foods]


async def foods_view(request: Request, user: User, errors: dict | None = None, typed: dict | None = None,
                     status_code: int = 200) -> Response:
    saved = await _db(_saved_foods, user.id)
    deleted = request.query_params.get("deleted")
    return page(request, "foods.html", status_code=status_code, foods=saved, errors=errors or {},
                typed=typed or {}, kg=rules.kg, saved=request.query_params.get("saved") == "1",
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
    food = next((f for f in await _db(_saved_foods, user.id) if str(f["id"]) == food_id), None)
    if food is None:
        return not_found_page(request)
    errors, status = {}, 200
    if request.method == "POST":
        form = await request.form()
        data = {**food_form(form), "name": food["name"]}
        if not changed_fields(FOOD_FIELDS[1:], food, form):
            return redirect(links.page("foods"))
        saved = await write(user, "save_food", services.save_food, user.id, data, "web")
        if "too_many_writes" in codes(saved):
            return too_many(request, saved)
        if not failed(saved):
            return redirect(links.page("foods") + "?saved=1")
        errors, status = field_errors("saved_food", saved), 400
        food = {**food, **{f: str(form.get(f) or "") for f in FOOD_FIELDS[1:]}}
    return page(request, "food_edit.html", status_code=status, food=food, errors=errors, kg=rules.kg)


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
    if not data:
        return redirect(links.page("settings") + "?saved=targets")
    result = await write(user, "update_settings", services.update_settings, user.id, data, "web")
    if "too_many_writes" in codes(result):
        return too_many(request, result)
    if failed(result):
        typed = {f: str(form.get(f) or "") for f in TARGET_FIELDS}
        return settings_view(request, user, target_errors=field_errors("settings", result), typed=typed,
                             status_code=400)
    return redirect(links.page("settings") + "?saved=targets")


# ---------------------------------------------------------------- Progress

def _trend(user_id: int) -> dict:
    """What /progress shows beyond get_progress's rows: each lift's best set (lifts.history, the numbers
    get_progress uses) and the body weight chart. bodyweight_chart takes the weigh-in and goal rows
    themselves, so this reads them. Nothing is written."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return {"best_sets": {}, "bodyweight": None, "weigh_ins": 0, "latest_weigh_in": None}
        today = users.today_for(user)
        best_sets = lifts.history(s, user.id, today)["best_set"]
        weighed = body.weigh_ins(s, user.id, today)
        goals = list(s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "open")))
        chart = progress_data.bodyweight_chart(weighed, goals, today)
        latest = weighed[-1] if weighed else None
        return {"best_sets": best_sets, "bodyweight": chart, "weigh_ins": len(weighed),
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
    return page(request, "progress.html", lifts=result["lifts"], best_sets=trend["best_sets"], trend=trend,
                chart=charts.bodyweight_svg(trend["bodyweight"]), kg=rules.kg, slug=links.slug)


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
    return page(request, "progress_lift.html", lift=lift, chart=charts.chart_svg(chart), kg=rules.kg,
                sessions=list(reversed(lift["estimated_max_over_time"])))


async def placeholder(request: Request) -> Response:
    """A page that is not built yet. Never repeats any part of the address."""
    if await current_user(request) is None:
        return redirect(links.page("sign_in"))
    return message(request, "Not built yet", "This page is not built yet. What you logged is saved.",
                   "Meanwhile, ask Claude to read your numbers back.")


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
]


def routes(settings: Settings) -> list[BaseRoute]:
    """The web routes. Each carries its own middleware, so /mcp and /.well-known never see the cookie."""
    reason = problem(settings)
    stack = [Middleware(SecurityHeaders, headers=headers_for(settings))]
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
        handler = _db_safe(endpoint)
        if any(m not in csrf.SAFE_METHODS for m in methods):
            handler = csrf.protect(handler, refuse)
        built.append(Route(path, handler, methods=methods, middleware=stack))
    built.append(Mount("/static", app=StaticFiles(directory=str(HERE / "static")), name="static",
                       middleware=[Middleware(SecurityHeaders, headers=headers_for(settings, static=True))]))
    return built
