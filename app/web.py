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

from sqlalchemy import select

from app import calllog, charts, csrf, db, links, rules, services, users, web_auth
from app.config import Settings, get_settings
from app.models import CLAUDE_CAN, MEASUREMENT_KINDS, Goal, User
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
    "foods": "/foods",
}

# The kinds of entry the web shows and edits, and the fields it offers for each (decision 7: sets are
# changed through Claude only in this stage).
EDIT_FIELDS = {"bodyweight": ("kg", "date"), "measurement": ("value", "date"), "lift_session": ("date", "notes")}
KIND_NAMES = {"bodyweight": "weigh-in", "measurement": "measurement", "lift_session": "lift session"}
DATE_ERRORS = {"invalid_date", "date_in_future", "date_too_old", "day_taken"}

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
                  status_code: int = 200) -> Response:
    groups, selected = zone_choices(user.time_zone)
    saved = request.query_params.get("saved")
    return page(request, "settings.html", status_code=status_code, user=user, error=error,
                saved=saved == "time_zone" and error is None, switch_saved=saved == "claude_can",
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
    return page(request, "day.html", status_code=status_code, day=day, on=on, today=today,
                prev_day=prev_day, next_day=next_day,
                kinds=measurement_kinds(), undo=undo, form_error=form_error or {}, kg=rules.kg,
                kind_names=KIND_NAMES, labels=MEASUREMENT_LABELS)


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


def field_errors(kind: str, result: dict, measured: str | None = None) -> dict:
    """{field: the page's own message}, from the codes of a refused write."""
    out = {}
    for code in codes(result):
        if code in DATE_ERRORS:
            field = "date"
        elif code == "invalid_kind":
            field = "kind"
        else:
            field = "notes" if kind == "lift_session" else EDIT_FIELDS[kind][0]
        out[field] = form_message(kind, field, measured)
    first = EDIT_FIELDS[kind][0] if kind != "lift_session" else "notes"
    return out or {first: form_message(kind, first, measured)}


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
    else:
        result = await write(user, "log_measurement", services.log_measurement, user.id,
                             {"kind": str(form.get("kind") or ""), "value": str(form.get("value") or ""),
                              "date": on.isoformat()}, "web")
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


async def log_measurement(request: Request) -> Response:
    return await log_on_day(request, "measurement")


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
    return page(request, "edit.html", status_code=status, kind=kind, kind_name=KIND_NAMES[kind],
                entry_id=found["id"], entry=entry, errors=errors, kg=rules.kg, labels=MEASUREMENT_LABELS)


def edit_changes(kind: str, entry: dict, form) -> dict:
    """Only the fields the person changed, as they typed them: the service checks and stores them."""
    changes = {}
    for field in EDIT_FIELDS[kind]:
        sent = str(form.get(field) or "")
        current = entry.get(field)
        if field in ("kg", "value"):
            same = rules.to_float(sent) is not None and current is not None and rules.to_float(sent) == float(current)
        elif field == "notes":
            same = sent.strip() == (current or "").strip()
        else:
            same = sent.strip() == str(current or "")
        if not same:
            changes[field] = sent
    return changes


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
    ("/entries/{kind}/{entry_id}/edit", edit_page, ["GET", "POST"]),
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
