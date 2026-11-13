"""Spotter's MCP server: nineteen functions and two resources, and owner_calls for Spotter's owner alone.

The MCP library does the sign-in work: it serves the protected-resource metadata, answers 401 with
`resource_metadata`, and runs every call through the token verifier. Functions read the verified token
the library stored for this request; nothing here reads the Authorization header.

Scopes: the metadata advertises SCOPES, and a client asks the provider for what is advertised there
(the library's challenges carry no `scope=` of their own). The server requires only REQUIRED_SCOPES.

One wrapper runs every function (Wrapper.call below). In order: it notes the start time, reads the
verified token, finds or creates the person's account (users.user_for_identity), asks the provider's
userinfo for a missing email or name (at most once per LOOKUP_EVERY seconds per person), starts the
call's call-log row (calllog.start), checks the person's permission switch against NEEDS, checks the
read limit for a read-level function (services.common.check_reads: every read together), runs the
service with the account id, turns a crash into {error: internal_error, ...}, and completes the row
(calllog.finish). The row exists before the service runs, so the write limit and the read limit count a
call from its start. A query cancelled by a database limit, a lock wait that ran out or no free pooled
connection is DATABASE_DOWN: database_unavailable, as for a database that is not there. The services,
the user lookup and the call log are synchronous and use the database, so each runs in a worker thread,
never on the event loop.

The row carries the call's trace (app/calllog.py): what was sent (SpotterServer.call_tool holds the
arguments as the client sent them), the outcome, the step, the frame and the notes.

owner_calls is listed only for the owner (SpotterServer.list_tools), and a call by anyone else is
not_allowed: both check the verified token's subject against OWNER_USER_ID (is_owner). Unset, nobody is.

Every result is the service's own dict, as structured content and as compact JSON text. A service
error ({error, message, fix}) is flagged is_error. A duplicate is a success.
"""

import functools
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar
from typing import Annotated, Any
from urllib.parse import urlparse

import anyio.to_thread
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.exc import InterfaceError, InternalError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.applications import Starlette
from starlette.routing import Route

from app import calllog, db, links, services, users
from app.auth import JwksTokenVerifier, UserinfoClient
from app.config import ROOT, Settings, get_settings
from app.mcp_schemas import (
    FOOD_NAME_RULE, IMPORT_ROWS, DeleteEntryResult, EditEntryResult, ExercisesResult, FindFoodResult, FoodEntryIn,
    GetGoalsResult, GetLogResult, GetProgressResult, GetTrendsResult, ImportEntriesResult, ImportRowIn,
    LogBodyweightResult, LogCardioResult, LogFoodResult, LogLiftsResult, LogMeasurementResult, OwnerCallsResult,
    RestoreEntryResult, SaveFoodResult, SetGoalResult, SetIn, UpdateSettingsResult, WhoamiResult,
)
from app.models import User
from app.services.common import check_reads
from app.services.owner import HOURS, LIMIT

log = logging.getLogger("spotter.mcp")

# The tag rule, in Claude's terms. Spotter derives the tag; this is what Claude sends for each kind of number.
# Which activity to log when the person's word fits more than one. Log, don't ask: the person corrects it.
CARDIO_RULE = (
    "When the person's words fit more than one activity (treadmill, outdoor, bike), log the one their words "
    "point to: a pace, speed or distance that means more than 7 km/h is a run, otherwise a walk; with nothing "
    "to go on, the walk, the lower estimate. Say in your reply which one was taken; the person can correct it "
    "with edit_entry. Never ask first."
)

# Bringing history over from another app's export: only what guards the person's data before the function is chosen.
# Everything else is in import_entries' own description (IMPORT_DESCRIPTION), which the model reads when it calls it:
# Claude Code cuts server instructions past 4,096 characters, so the instructions carry only these five guards.
IMPORT_RULE = (
    "To bring history over from another app, read its export and send it with import_entries, after reading that "
    "function's description. The first call is a preview: show the person what would be written, what is already "
    "there and what was refused. Write, with dry_run false, only when the person says yes. Send numbers as the "
    "export writes them and name the unit of each: never convert one, never assume one. Use names only from "
    "list_exercises. Ask the person about any other name: never guess."
)

# import_entries' description: what the long import rule said before, beside what the function does. Its promises
# are the service as built (app.services.imports), and the known limit is in can-and-cant.
IMPORT_DESCRIPTION = (
    "Brings history over from another app's export: lift sessions, weigh-ins, measurements, food and cardio. Each "
    "row is dated, and takes kind, date and the named fields of its kind. "
    "A preview first. With dry_run true or left out, nothing is written: the result says what would be written, "
    "what is already there, what falls on a day Spotter already holds, and what was refused and why. Show the "
    "person that, and the units the preview names, and send the same rows with dry_run false only when they say "
    "yes. "
    "Units. Send weights, distances, speeds and body sizes as the export writes them: never convert them "
    "yourself. Send the unit of every weight, distance and body size the rows hold, in weight_unit, "
    "distance_unit and length_unit. Spotter converts them and never assumes a unit: a call whose rows hold a "
    "weight, a distance, a speed or a body size without its unit is refused, a preview too. When the export does "
    "not say which unit it uses, ask the person: never guess. One call names one unit for each quantity: rows in "
    "another unit go in a separate call. When one day's sessions, or one session, mix units, ask the person. A "
    "row's speed is in the call's distance unit an hour. "
    "Size. Up to 200 rows and up to 1,000 sets and food entries a call, oldest first, with one day's rows of a "
    "kind in the same call. Send the rest in the next call. For food at six entries a day, and for lifting with "
    "one item a set and more than 5 sets a session, the 1,000 sets and food entries run out before the 200 rows: "
    "send times for sets that repeat. At most 10,000 imported rows in any 24 hours. "
    "Dates. Send each row's timestamp with its offset when the export gives one, or its date. Leave out rows "
    "more than 2 years old, and say how many were left out. "
    "Names. Use exercise and activity names only from list_exercises, exactly as it gives them. When a name in "
    "the export is not on the list, ask the person once per name which listed name it is, or whether to leave it "
    "out. The closest names the result gives can be wrong. Never pick one on a guess, and never leave a lift out "
    "without saying so. "
    "Health stores and food. From a phone's health store, send one value a day: the day's total steps, the "
    "night's total sleep, the day's resting heart rate as the app gives it, and the day's first weigh-in. Send "
    "each food entry's meal as the export names it. Send a food entry's calories only as the export gives them: "
    "never estimate one for an import. A food entry with no calories is left out: tell the person how many. "
    "When the export gives only a meal's or a day's total, send it as one entry described as that total, such as "
    "Breakfast from the old app. For an import only, when the export gives only a day's total, ask the person "
    "once which meal such totals go under, and use it for every such row: logging food still never asks. "
    "Sending again. An import never replaces an entry. Sent again with the same time zone set, the same rows "
    "write nothing that is already there, and an imported entry the person edited, moved or deleted counts as "
    "already there. Two cases are the exception, both named in the can-and-cant resource: one food case, and "
    "rows stamped in UTC after the person's time zone changed."
)

# Which entry to delete. Unlike the activity, a delete is never a guess.
DELETE_RULE = (
    "Delete only the entry the person named. When their words fit more than one entry, ask which; never "
    "delete on a guess. After a delete, say what was deleted and that restore_entry brings it back."
)

FOOD_RULE = (
    "A number the person stated goes as kcal, with estimated false. A number you worked out goes as kcal with "
    "estimated true, always, and you say in your reply that it is an estimate. A saved or USDA food from "
    "find_food goes by its id with servings or grams, and no kcal: Spotter works out the number. If find_food "
    "has no match and the person gave no number, you may estimate, and must say so."
)

# Which read answers which question. get_trends and get_goals carry the same text.
TRENDS_RULE = "To answer how the person is doing, call get_trends."
GOALS_RULE = "To answer whether the person is on track, call get_goals."

# A goal "by the end of March" names a month, not a day. set_goal's target_date carries the same text.
MONTH_END_RULE = (
    "A goal by the end of a month, such as by the end of March, takes the last day of that month as target_date: "
    "the next such day after today, worked out from today's date."
)

# The two acts that stay the person's own. No function does either, so Claude never offers one.
SETTINGS_ONLY_RULE = (
    "Exporting the person's data and deleting their account are the person's own clicks on Settings: no function "
    "does either. Never offer to do them; say they are on Settings."
)

# How Claude words a reply: Spotter's figures as given, with their units, and no dashes between clauses.
# Its last sentence names plan_line, which every goal's numbers carry from stage 6 step 3 (GoalNumbers).
REPLY_RULE = (
    "How to reply: plain words, one fact a line. Quote each number as Spotter gives it, with its unit: never add "
    "decimals, never round further, and never recalculate one yourself. Where a summary gives a figure, say the "
    "summary's figure. Say \"about\" as a word, never as a symbol. No dashes between clauses: use a colon, a comma "
    "or a full stop. Name an estimate as an estimate. Where a goal carries plan_line, say it as written, "
    "and do not also quote latest, should_be_today or ahead_by."
)

INSTRUCTIONS = (
    "Spotter is the person's own record of lifts, body weight, measurements, food, cardio and goals. "
    "Call whoami first: it says whose account this is and what today's date is for them. "
    "If whoami carries time_zone_note, set the time zone with update_settings before logging anything: use the "
    "person's time zone if you already know it, otherwise ask once which city they are in. Never send the person "
    "to a page for it. "
    "Use exercise names exactly as list_exercises gives them. "
    "Leave the date out for something done today: today is the person's own date. "
    "Quote Spotter's numbers; never estimate your own, except a food's kcal, as the food rule says. "
    "If a call returns an error, read its fix and retry. "
    "duplicate: true means nothing was written: say so, and send allow_repeat: true only when the person "
    "confirms it was a separate entry. "
    "To answer what the person did on a day or in a week, use get_log. "
    + TRENDS_RULE + " " + GOALS_RULE + " "
    "Goals: " + MONTH_END_RULE + " "
    "Food: " + FOOD_RULE + " " + FOOD_NAME_RULE + " "
    "Leave the meal out unless the person named it: Spotter picks it from their clock. Never ask which meal. "
    "Cardio: " + CARDIO_RULE + " "
    "Importing: " + IMPORT_RULE + " "
    "Deleting: " + DELETE_RULE + " "
    + SETTINGS_ONLY_RULE + " "
    "If a call returns not_allowed, tell the person what this account lets Claude do, and do not retry: "
    "only the person can change it. "
    "Put links last. "
    + REPLY_RULE
)

# What clients are told to ask for. offline_access is what gets a client a refresh token.
SCOPES = ["openid", "profile", "email", "offline_access"]
# What a token must carry to get in: openid, without which the provider's userinfo cannot name the person.
REQUIRED_SCOPES = ["openid"]

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Nothing is hard-deleted: a replaced weigh-in is kept and can be restored. So no write is destructive.
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
# Setting the same value twice changes nothing the second time.
SETTING = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# A delete is undone by restore_entry, but it still needs the person's go-ahead: the description says so too.
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)

# What each function needs the person's permission switch (claude_can) to be at least.
LEVELS = ("read", "log", "log_delete")
NEEDS = {
    "whoami": "read", "list_exercises": "read", "get_progress": "read", "get_log": "read",
    "log_lifts": "log", "log_bodyweight": "log", "set_goal": "log", "update_settings": "log",
    "log_measurement": "log", "edit_entry": "log", "restore_entry": "log",
    "find_food": "read", "log_food": "log", "save_food": "log", "log_cardio": "log",
    "delete_entry": "log_delete",
    "get_trends": "read", "get_goals": "read",
    "import_entries": "log",
    "owner_calls": "read",
}
# Listed for the owner alone, and refused to anyone else (is_owner).
OWNER_FUNCTION = "owner_calls"
# The read limit counts these together (services.common.check_reads): every function at the read level.
READ_FUNCTIONS = tuple(name for name, level in NEEDS.items() if level == "read")
NOT_ALLOWED_MESSAGES = {"read": "This account lets Claude read only.",
                        "log": "This account lets Claude read and log, not delete."}
# The one text in Spotter that sends a person to a page: only they can change what Claude may do.
NOT_ALLOWED_FIX = "The person can change what Claude may do on the Settings page."
OWNER_ONLY = {"error": "not_allowed", "message": "This function is for Spotter's owner.", "fix": "Nothing to retry."}
# The arguments of the call running, as the client sent them (SpotterServer.call_tool): what tried describes.
_SENT: ContextVar[dict | None] = ContextVar("spotter_sent_arguments", default=None)

LOG_DAYS = (1, 14)  # what get_log takes; the service judges, the schema says it
KIND_DESCRIPTION = "lift_session, bodyweight, measurement, food_entry or cardio_entry."
ENTRY_ID_DESCRIPTION = ("The entry's id, from log_lifts, log_bodyweight, log_measurement, log_food, log_cardio "
                        "or get_log, "
                        "with the kind it came with.")

CAN_AND_CANT = ROOT / "docs" / "can-and-cant.md"
CAN_AND_CANT_URI = "spotter://can-and-cant"
EXERCISES_URI = "spotter://exercises"

# Only these mean "the database is not there" (or too busy to answer: every pooled connection is in use, or
# a statement or lock wait ran past its limit, which psycopg raises as an OperationalError, or a transaction
# left idle past its limit, which psycopg raises as an InternalError). Any other error is a crash.
DATABASE_DOWN = (OperationalError, InterfaceError, InternalError, PoolTimeout, db.DatabaseNotConfigured)

# The MCP library logs at INFO on every call, and repeats a caller-chosen tool name when it refuses one.
# Its loggers (under "mcp") are held at WARNING: its warnings and errors still show.
LIBRARY_LOG_LEVEL = "WARNING"

LOOKUP_EVERY = 600.0  # seconds: userinfo is asked about one person at most this often
LOOKUP_MEMO_SIZE = 1024  # people remembered; the oldest is forgotten first


def clock() -> float:
    """The memo's clock. Tests replace it."""
    return time.monotonic()


def _error(code: str, message: str, fix: str) -> dict:
    return {"error": code, "message": message, "fix": fix}


def internal_error(function: str) -> dict:
    return _error("internal_error", f"{function} failed inside Spotter.",
                  "Retry once. If it fails again, tell the person Spotter had a problem.")


def database_unavailable() -> dict:
    return _error("database_unavailable", "Spotter cannot reach its database right now.",
                  "Wait a minute and retry. If it keeps failing, tell the person Spotter is down.")


def _json_text(result: dict) -> tuple[str, int]:
    """The result as compact JSON text, and its size in UTF-8 bytes. Raises when it cannot be sent as
    UTF-8 (a lone surrogate, say), so the caller can answer internal_error instead."""
    text = json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text, len(text.encode("utf-8"))


def level_of(claude_can) -> str:
    """The switch's level. Anything that is not one of LEVELS counts as the lowest, read."""
    return claude_can if claude_can in LEVELS else "read"


def not_allowed(function: str, claude_can) -> dict | None:
    """None when the switch allows this function; else the not_allowed answer. A function missing
    from NEEDS needs the most, so a new function is refused rather than let through."""
    have = level_of(claude_can)
    if LEVELS.index(have) >= LEVELS.index(NEEDS.get(function, LEVELS[-1])):
        return None
    return {**_error("not_allowed", NOT_ALLOWED_MESSAGES[have], NOT_ALLOWED_FIX),
            "links": {"settings": links.page("settings")}}


def is_owner(token: AccessToken | None) -> bool:
    """True when the verified token's subject is OWNER_USER_ID. Unset, nobody is."""
    owner = get_settings().owner_user_id
    return bool(owner) and token is not None and token.subject == owner


def _claim(claims: dict, key: str) -> str | None:
    value = claims.get(key)
    return value if isinstance(value, str) else None


class LookupMemo:
    """When userinfo was last asked about each subject, so a person whose token cannot be looked up
    does not cost a network call on every function. Bounded: the oldest entry goes first."""

    def __init__(self, every: float = LOOKUP_EVERY, size: int = LOOKUP_MEMO_SIZE) -> None:
        self.every = every
        self.size = size
        self._asked: OrderedDict[str, float] = OrderedDict()

    def should_ask(self, subject: str) -> bool:
        """True, and the attempt is remembered, unless this subject was asked about within the window."""
        now = clock()
        last = self._asked.get(subject)
        if last is not None and now - last < self.every:
            return False
        self._asked[subject] = now
        self._asked.move_to_end(subject)
        while len(self._asked) > self.size:
            self._asked.popitem(last=False)
        return True


class SpotterServer(MCPServer):
    """MCPServer whose metadata advertises SCOPES while tokens need only REQUIRED_SCOPES.

    mcp 2.3.0 feeds one setting, required_scopes, to both the scope check and the metadata's
    scopes_supported. So the app is built as usual, with REQUIRED_SCOPES, and its one metadata route is
    swapped for the library's own, built with SCOPES. Nothing of the route is written here.
    """

    def streamable_http_app(self, **kwargs) -> Starlette:
        app = super().streamable_http_app(**kwargs)
        auth = self.settings.auth
        path = urlparse(str(build_resource_metadata_url(auth.resource_server_url))).path
        found = [i for i, r in enumerate(app.router.routes) if isinstance(r, Route) and r.path == path]
        routes = create_protected_resource_routes(
            resource_url=auth.resource_server_url,
            authorization_servers=[auth.issuer_url],
            scopes_supported=SCOPES,
        )
        if len(found) != 1 or len(routes) != 1:  # the library changed shape: fail at start, not silently
            raise RuntimeError(f"expected one metadata route at {path}, found {len(found)}")
        app.router.routes[found[0]] = routes[0]
        return app

    async def list_tools(self) -> list:
        """Every function for the owner; for everyone else, all but OWNER_FUNCTION, exactly as before it."""
        tools = await super().list_tools()
        return tools if is_owner(get_access_token()) else [t for t in tools if t.name != OWNER_FUNCTION]

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        """The library's call, with the arguments as sent held for the wrapper (tried) while it runs."""
        held = _SENT.set(arguments)
        try:
            return await super().call_tool(name, arguments, context)
        finally:
            _SENT.reset(held)


class Wrapper:
    """Runs one function call from start to call-log row. See the module notes for the order."""

    def __init__(self, userinfo: UserinfoClient) -> None:
        self.userinfo = userinfo
        self.memo = LookupMemo()

    async def _account(self, token: AccessToken | None) -> User | dict:
        """The caller's user row, or the error dict from the lookup. Asks userinfo for what is missing."""
        subject = token.subject if token is not None else None
        claims = (token.claims if token is not None else None) or {}
        issuer = _claim(claims, "iss")
        user = await anyio.to_thread.run_sync(
            users.user_for_identity, subject, _claim(claims, "email"), _claim(claims, "name"), issuer)
        if isinstance(user, dict) or token is None:
            return user
        if (user.email is None or user.name is None) and self.memo.should_ask(user.subject):
            email, name = await self.userinfo.lookup(token.token, user.subject)
            if email is not None or name is not None:
                found = await anyio.to_thread.run_sync(users.user_for_identity, subject, email, name, issuer)
                user = found if not isinstance(found, dict) else user
        return user

    async def call(self, function: str, model: type[BaseModel], work: Callable[[int], dict],
                   add: Callable[[dict, AccessToken], dict] | None = None) -> CallToolResult:
        """Run `work(account_id)` in a worker thread and return its dict as the call's result."""
        started_at = db.utcnow()
        start = time.monotonic()
        account_id = row_id = done = frame = None  # done: the work's own result, once it has one
        started = reply = False
        tried = calllog.tried(function, _SENT.get())
        notes = calllog.new_notes()  # in the context before the worker thread starts: its notes reach the row
        try:
            token = get_access_token()
            user = await self._account(token)
            account_id = None if isinstance(user, dict) else user.id
            # The row exists before the service runs, so the write limit counts this call from its start.
            row_id = await _start_row(function, started_at, account_id, tried)
            started = True
            if isinstance(user, dict):
                result = user
            else:
                refused = not_allowed(function, user.claude_can)  # before the service: nothing is written
                if refused is None and function == OWNER_FUNCTION and not is_owner(token):
                    refused = {**OWNER_ONLY}
                if refused is None and function in READ_FUNCTIONS:  # the read limit, before the read's work
                    refused = await anyio.to_thread.run_sync(check_reads, account_id, READ_FUNCTIONS)
                result = refused or await anyio.to_thread.run_sync(work, account_id)
                if add is not None and "error" not in result:
                    result = add(result, token)
            done, reply = result, True
            is_error = "error" in result
            if not is_error:
                model.model_validate(result)  # a result that breaks its schema is Spotter's bug, not the caller's
            # Raises on anything that cannot be sent, such as a lone surrogate: then the answer is internal_error.
            text, size = _json_text(result)
            reply = False
        except DATABASE_DOWN as exc:
            log.warning("MCP %s could not use the database: %s", function, type(exc).__name__)
            result, is_error, frame = database_unavailable(), True, calllog.frame_of(exc)
            text, size = _json_text(result)
        except Exception as exc:  # noqa: BLE001 - the caller gets a typed error; the log gets type and frames
            log.error("MCP %s crashed: %s", function, calllog.crash_text(exc))
            result, is_error, frame = internal_error(function), True, calllog.frame_of(exc)
            text, size = _json_text(result)

        ms = int((time.monotonic() - start) * 1000)
        code = result.get("error") if is_error else None
        # A reply that failed after the work finished reads as what the work did: a write that landed is written.
        trace = (calllog.outcome(done, NEEDS.get(function) != "read"), calllog.step(code, reply), frame, notes)
        try:
            if not started:  # the call failed before its row was written (finding the person, say)
                row_id = await _start_row(function, started_at, account_id, tried)
            await anyio.to_thread.run_sync(calllog.finish, row_id, ms, not is_error, code, size, *trace)
        except Exception as exc:  # noqa: BLE001 - a lost log row never costs the person their answer
            log.warning("MCP %s: the call log could not be written: %s", function, type(exc).__name__)
        return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=result,
                              is_error=is_error)


async def _start_row(function: str, started_at, account_id: int | None, tried: str) -> int | None:
    """The call's call-log row, started in a worker thread. Any failure is None: the call still runs."""
    try:
        return await anyio.to_thread.run_sync(calllog.start, function, started_at, account_id, "claude", tried)
    except Exception as exc:  # noqa: BLE001 - a lost log row never costs the person their answer
        log.warning("MCP %s: the call log could not be written: %s", function, type(exc).__name__)
        return None


def _sign_in(result: dict, token: AccessToken) -> dict:
    """whoami's result plus the sign-in block, from the verified token."""
    claims = token.claims or {}
    return {**result, "sign_in": {"client_id": token.client_id, "scopes": token.scopes,
                                  "issuer": _claim(claims, "iss") or "", "expires_at": token.expires_at or 0}}


def _sent(**values) -> dict:
    """The arguments the caller sent, as the service's data dict. Left out: None, so service defaults apply."""
    return {k: v for k, v in values.items() if v is not None and v is not NOT_SENT}


class _NotSent:
    """The default of an argument where an explicit null means something (clear a target). The library
    validates no default and publishes none from a factory, so the argument stays optional; a call that
    leaves it out gets this marker, and one that sends null gets None."""

    def __repr__(self) -> str:
        return "NOT_SENT"


NOT_SENT = _NotSent()


def _nullable(**values) -> dict:
    """The arguments the caller sent, nulls included: only the ones left out are dropped."""
    return {k: v for k, v in values.items() if v is not NOT_SENT}


def not_sent():
    """A parameter default: Field(default_factory=...) keeps the marker out of the published schema."""
    return Field(default_factory=lambda: NOT_SENT)


def build_server(settings: Settings, verifier: JwksTokenVerifier, userinfo: UserinfoClient) -> MCPServer:
    """A fresh server per app: the library's session manager can be started only once."""
    server = SpotterServer(
        name="Spotter",
        version="0.1",
        instructions=INSTRUCTIONS,
        log_level=LIBRARY_LOG_LEVEL,  # the library's own logging.basicConfig would otherwise set INFO
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=settings.issuer,
            resource_server_url=settings.mcp_resource_url,
            required_scopes=REQUIRED_SCOPES,  # a token without one gets 403 insufficient_scope
            # The real audience check is the verifier's exact match on aud. The verifier reports the configured
            # address as the token's resource, so this setting only compares that address with itself.
            validate_token_resource=True,
        ),
    )
    # Also set on the logger itself, so it holds even when something else configured logging first.
    logging.getLogger("mcp").setLevel(LIBRARY_LOG_LEVEL)
    wrapper = Wrapper(userinfo)

    @server.tool(
        name="whoami",
        title="Who am I",
        description=(
            "Says whose Spotter account this is: the account id, the sign-in id, email, name, time zone, "
            "today's date for the person, their targets, what Claude may do, and the sign-in itself. "
            "Call it first. If it carries time_zone_note, set the time zone with update_settings before logging "
            "anything: use the person's zone if you know it, otherwise ask once which city they are in."
        ),
        annotations=READ_ONLY,
    )
    async def whoami() -> Annotated[CallToolResult, WhoamiResult]:
        return await wrapper.call("whoami", WhoamiResult, services.whoami, add=_sign_in)

    @server.tool(
        name="list_exercises",
        title="List exercises",
        description=(
            "The exercise names Spotter knows. Every other function takes a name exactly as written here. "
            "Filter by kind or category, or leave both out for the whole list."
        ),
        annotations=READ_ONLY,
    )
    async def list_exercises(
        kind: Annotated[str | None, Field(description="lift or cardio. Leave out for all.")] = None,
        category: Annotated[str | None, Field(
            description="For lifts: squat, hinge, push, pull, carry, core or accessory. For cardio: walk, run, "
                        "cycle, row, swim, machine or other. Leave out for all.")] = None,
    ) -> Annotated[CallToolResult, ExercisesResult]:
        return await wrapper.call("list_exercises", ExercisesResult,
                                  lambda _account: services.list_exercises(kind, category))

    @server.tool(
        name="log_lifts",
        title="Log a lift session",
        description=(
            "Records one lift session: its sets, and optional date and notes. Returns the saved session, any new "
            "bests, and a one-line summary. If the same session was logged in the last 10 minutes, nothing is "
            "written and duplicate is true."
        ),
        annotations=WRITE,
    )
    async def log_lifts(
        sets: Annotated[list[SetIn], Field(description="Every set done, in order: 1 to 60 sets.")],
        date: Annotated[str | None, Field(
            description="The day it happened, YYYY-MM-DD, at most 2 years back. Leave out for today.")] = None,
        notes: Annotated[str | None, Field(
            description="How it went, in the person's words. Up to 2,000 characters.")] = None,
        allow_repeat: Annotated[bool, Field(
            description="true logs it even when it matches a session from the last 10 minutes. Send it only "
                        "when the person confirms it was a separate session.")] = False,
    ) -> Annotated[CallToolResult, LogLiftsResult]:
        data = _sent(sets=[s.model_dump() for s in sets], date=date, notes=notes, allow_repeat=allow_repeat or None)
        return await wrapper.call("log_lifts", LogLiftsResult,
                                  lambda account: services.log_lifts(account, data, "claude"))

    @server.tool(
        name="log_bodyweight",
        title="Log body weight",
        description=(
            "Records one weigh-in and returns the latest weigh-ins and the trend. One weigh-in a day is kept: "
            "a different weight on the same day replaces the earlier one, which is kept, not erased. The same "
            "weight again within 10 minutes is a duplicate: nothing is written."
        ),
        annotations=WRITE,
    )
    async def log_bodyweight(
        kg: Annotated[float, Field(description="Body weight in kg, 20 to 400.")],
        date: Annotated[str | None, Field(
            description="The day, YYYY-MM-DD, at most 2 years back. Leave out for today.")] = None,
        allow_repeat: Annotated[bool, Field(
            description="true logs it even when it matches a weigh-in from the last 10 minutes.")] = False,
    ) -> Annotated[CallToolResult, LogBodyweightResult]:
        data = _sent(kg=kg, date=date, allow_repeat=allow_repeat or None)
        return await wrapper.call("log_bodyweight", LogBodyweightResult,
                                  lambda account: services.log_bodyweight(account, data, "claude"))

    @server.tool(
        name="set_goal",
        title="Set or change a goal",
        description=(
            "Sets a goal with a target and a date, of one of four kinds: lift (an estimated one-rep max to reach), "
            "bodyweight, measurement (a body size or resting heart rate to reach) or cardio_best (a time for a "
            "distance in one cardio activity). Spotter stores where it starts and which way it points, and returns "
            "where it stands. With goal_id, changes one of the person's goals instead: its target, date, text, or "
            "status (hit, dropped, open). A weekly amount, such as sessions or cardio minutes a week, is a target "
            "for update_settings, not a goal. " + MONTH_END_RULE
        ),
        annotations=WRITE,
    )
    async def set_goal(
        kind: Annotated[str | None, Field(
            description="lift, bodyweight, measurement or cardio_best. Needed for a new goal; leave out with "
                        "goal_id.")] = None,
        exercise: Annotated[str | None, Field(
            description="A lift goal: the lift. A cardio_best goal: the cardio activity. Each exactly as "
                        "list_exercises gives it.")] = None,
        measurement_kind: Annotated[str | None, Field(
            description="A measurement goal: waist, hips, chest, arm, thigh or resting_hr. Not steps, sleep_hours "
                        "or water_ml: those are a day's amount, not a goal.")] = None,
        distance_km: Annotated[float | None, Field(
            description="A cardio_best goal: the distance in km, 0.1 to 300. A session at or over it counts.")] = None,
        target_value: Annotated[float | None, Field(
            description="The target, in the kind's unit. A lift: kg, an estimated one-rep max, above 0 to 666.67. "
                        "Body weight: kg, 20 to 400. A measurement: its own unit, cm for a body size, bpm for "
                        "resting_hr. A cardio_best: minutes for the distance, 1 to 600.")] = None,
        target_date: Annotated[str | None, Field(
            description="The date to reach it by, YYYY-MM-DD, after today. " + MONTH_END_RULE)] = None,
        text: Annotated[str | None, Field(
            description="The goal in the person's words. Up to 2,000 characters.")] = None,
        start_value: Annotated[float | None, Field(
            description="Where the person is now, in the kind's unit. Used only when nothing is logged yet to start "
                        "from.")] = None,
        goal_id: Annotated[int | None, Field(
            description="To change a goal: its id, from set_goal, get_goals or get_progress. Leave out for a new "
                        "goal.")] = None,
        status: Annotated[str | None, Field(
            description="With goal_id: hit (reached), dropped, or open (reopen).")] = None,
    ) -> Annotated[CallToolResult, SetGoalResult]:
        data = _sent(kind=kind, exercise=exercise, measurement_kind=measurement_kind, distance_km=distance_km,
                     target_value=target_value, target_date=target_date, text=text, start_value=start_value,
                     goal_id=goal_id, status=status)
        return await wrapper.call("set_goal", SetGoalResult,
                                  lambda account: services.set_goal(account, data, "claude"))

    @server.tool(
        name="get_progress",
        title="Get progress",
        description=(
            "With an exercise: that lift over time, with its estimated max per session, best set, new bests, "
            "trend and open goals. Without: one line per lift logged, and every open goal. Quote these numbers."
        ),
        annotations=READ_ONLY,
    )
    async def get_progress(
        exercise: Annotated[str | None, Field(
            description="A lift or a cardio activity, as list_exercises gives it. Leave out for every lift.")] = None,
    ) -> Annotated[CallToolResult, GetProgressResult]:
        return await wrapper.call("get_progress", GetProgressResult,
                                  lambda account: services.get_progress(account, exercise))

    @server.tool(
        name="update_settings",
        title="Update settings",
        description=(
            "Sets the person's time zone and targets. The time zone decides what today is for them: give an "
            "IANA name such as Asia/Kolkata or Europe/London, worked out from the city they are in; an "
            "abbreviation (IST) or an offset (+05:30) is refused. The targets: daily calories, sessions a week, "
            "cardio minutes a week, and whether cardio calories are added back to the day's food. Send only what "
            "the person asked to change; a target sent as null is cleared. It cannot change what Claude may do: "
            "only the person can."
        ),
        annotations=SETTING,
    )
    async def update_settings(
        time_zone: Annotated[str | None, Field(
            description="An IANA time zone name, such as Asia/Kolkata, from the city the person is in. "
                        "Not an abbreviation or an offset.")] = None,
        calorie_target_kcal: Annotated[int | None, Field(
            description="The daily calorie target, 800 to 10,000. null clears it.")] = not_sent(),
        target_sessions_per_week: Annotated[int | None, Field(
            description="Training sessions a week the person aims for, 0 to 14. null clears it.")] = not_sent(),
        target_cardio_minutes_per_week: Annotated[int | None, Field(
            description="Cardio minutes a week the person aims for, 0 to 3,000. null clears it.")] = not_sent(),
        add_back_exercise_kcal: Annotated[bool | None, Field(
            description="true adds cardio calories back to the day's food total (net calories); false stops it.")]
            = not_sent(),
    ) -> Annotated[CallToolResult, UpdateSettingsResult]:
        # A target sent as null is passed on, so the service clears it; one left out is not passed at all.
        data = {**_sent(time_zone=time_zone),
                **_nullable(calorie_target_kcal=calorie_target_kcal, target_sessions_per_week=target_sessions_per_week,
                            target_cardio_minutes_per_week=target_cardio_minutes_per_week,
                            add_back_exercise_kcal=add_back_exercise_kcal)}
        return await wrapper.call("update_settings", UpdateSettingsResult,
                                  lambda account: services.update_settings(account, data, "claude"))

    @server.tool(
        name="get_log",
        title="Get the log",
        description=(
            "What the person logged, day by day: each day's lift sessions with their sets and bests, the "
            "weigh-in and the measurements. One day by default, up to 14, ending on the date given or today. "
            "Use it to answer what the person did on a day or in a week. The ids it gives work in edit_entry, "
            "delete_entry and restore_entry."
        ),
        annotations=READ_ONLY,
    )
    async def get_log(
        date: Annotated[str | None, Field(
            description="The last day to show, YYYY-MM-DD. Leave out for today.")] = None,
        days: Annotated[int | None, Field(
            description="How many days to show, ending on date: 1 to 14. Leave out for one day.",
            json_schema_extra={"minimum": LOG_DAYS[0], "maximum": LOG_DAYS[1]})] = None,
    ) -> Annotated[CallToolResult, GetLogResult]:
        sent = _sent(date=date, days=days)
        return await wrapper.call("get_log", GetLogResult, lambda account: services.get_log(account, **sent))

    @server.tool(
        name="log_measurement",
        title="Log a measurement",
        description=(
            "Records one body measurement: waist, hips, chest, arm or thigh in cm, steps, sleep_hours, "
            "resting_hr in bpm, or water_ml. One value per kind a day: a different value on the same day "
            "replaces the earlier one, which is kept, not erased. The same value again within 10 minutes is a "
            "duplicate: nothing is written."
        ),
        annotations=WRITE,
    )
    async def log_measurement(
        kind: Annotated[str, Field(
            description="waist, hips, chest, arm, thigh, steps, sleep_hours, resting_hr or water_ml.")],
        value: Annotated[float, Field(
            description="The value in the kind's unit: cm for the body sizes, steps, hours of sleep, bpm, ml.")],
        date: Annotated[str | None, Field(
            description="The day, YYYY-MM-DD, at most 2 years back. Leave out for today.")] = None,
        allow_repeat: Annotated[bool, Field(
            description="true logs it even when it matches a measurement from the last 10 minutes.")] = False,
    ) -> Annotated[CallToolResult, LogMeasurementResult]:
        data = _sent(kind=kind, value=value, date=date, allow_repeat=allow_repeat or None)
        return await wrapper.call("log_measurement", LogMeasurementResult,
                                  lambda account: services.log_measurement(account, data, "claude"))

    @server.tool(
        name="edit_entry",
        title="Edit an entry",
        description=(
            "Changes one of the person's entries in place, keeping its id. A lift session: its date, notes or "
            "whole set list. A weigh-in: its kg or date. A measurement: its value or date. A food entry: its "
            "date, meal, description or kcal; a kcal makes it the person's own number, tagged you_said. A cardio "
            "entry: its date, activity, minutes, distance, incline, speed, heart rate, machine calories or notes; "
            "its estimate is worked out again. Send only what changes. Returns the entry before and after. "
            "Moving a weigh-in or measurement onto a day that already has one is refused, never silently replaced."
        ),
        annotations=WRITE,
    )
    async def edit_entry(
        kind: Annotated[str, Field(description=KIND_DESCRIPTION)],
        entry_id: Annotated[int, Field(description=ENTRY_ID_DESCRIPTION)],
        date: Annotated[str | None, Field(description="The new date, YYYY-MM-DD.")] = None,
        notes: Annotated[str | None, Field(
            description="A lift session's or cardio entry's new notes, up to 2,000 characters. null clears them.")]
            = not_sent(),
        sets: Annotated[list[SetIn] | None, Field(
            description="A lift session's whole new set list, in order: 1 to 60 sets. It replaces every set.")] = None,
        kg: Annotated[float | None, Field(description="A weigh-in's new weight in kg, 20 to 400.")] = None,
        value: Annotated[float | None, Field(description="A measurement's new value, in its unit.")] = None,
        meal: Annotated[str | None, Field(description="A food entry's meal: breakfast, lunch, dinner or snack.")] = None,
        description: Annotated[str | None, Field(description="A food entry's new description.")] = None,
        kcal: Annotated[float | None, Field(
            description="A food entry's calories, as the person stated them. It becomes their own number.")] = None,
        exercise: Annotated[str | None, Field(
            description="A cardio entry's activity, as list_exercises gives it.")] = None,
        minutes: Annotated[float | None, Field(description="A cardio entry's minutes, 1 to 600.")] = None,
        distance_km: Annotated[float | None, Field(
            description="A cardio entry's distance in km. null clears it.")] = not_sent(),
        incline_pct: Annotated[float | None, Field(
            description="A cardio entry's incline in percent. null clears it.")] = not_sent(),
        speed_kmh: Annotated[float | None, Field(
            description="A cardio entry's speed in km/h. null works it out from distance and minutes.")] = not_sent(),
        avg_hr: Annotated[int | None, Field(
            description="A cardio entry's average heart rate in bpm. null clears it.")] = not_sent(),
        machine_kcal: Annotated[int | None, Field(
            description="The calories the machine or watch showed. null clears it.")] = not_sent(),
    ) -> Annotated[CallToolResult, EditEntryResult]:
        # The cardio extras and notes pass an explicit null on (it clears them, or works the speed out); every
        # other field sent as null is the same as not sent.
        changes = {**_sent(date=date, sets=None if sets is None else [x.model_dump() for x in sets], kg=kg,
                           value=value, meal=meal, description=description, kcal=kcal, exercise=exercise,
                           minutes=minutes),
                   **_nullable(notes=notes, distance_km=distance_km, incline_pct=incline_pct, speed_kmh=speed_kmh,
                               avg_hr=avg_hr, machine_kcal=machine_kcal)}
        return await wrapper.call("edit_entry", EditEntryResult,
                                  lambda account: services.edit_entry(account, kind, entry_id, changes, "claude"))

    @server.tool(
        name="restore_entry",
        title="Restore an entry",
        description=(
            "Brings back a deleted or replaced entry. If the day already has a live one of the same kind, the "
            "two swap: the live one is set aside and can itself be restored."
        ),
        annotations=WRITE,
    )
    async def restore_entry(
        kind: Annotated[str, Field(description=KIND_DESCRIPTION)],
        entry_id: Annotated[int, Field(description=ENTRY_ID_DESCRIPTION)],
    ) -> Annotated[CallToolResult, RestoreEntryResult]:
        return await wrapper.call("restore_entry", RestoreEntryResult,
                                  lambda account: services.restore_entry(account, kind, entry_id))

    @server.tool(
        name="delete_entry",
        title="Delete an entry",
        description=(
            "Deletes one of the person's entries. Nothing is erased: restore_entry brings it back, but a delete "
            "still needs the person's go-ahead first. " + DELETE_RULE + " Allowed only when the person lets Claude "
            "delete; otherwise the answer is not_allowed."
        ),
        annotations=DELETE,
    )
    async def delete_entry(
        kind: Annotated[str, Field(description=KIND_DESCRIPTION)],
        entry_id: Annotated[int, Field(description=ENTRY_ID_DESCRIPTION)],
    ) -> Annotated[CallToolResult, DeleteEntryResult]:
        return await wrapper.call("delete_entry", DeleteEntryResult,
                                  lambda account: services.delete_entry(account, kind, entry_id))

    @server.tool(
        name="find_food",
        title="Find a food",
        description=(
            "Looks a food up before logging it: the person's saved foods and their own recent entries first, "
            "then the USDA table when it is on. Log a saved or USDA food by its id and an amount, with no kcal: "
            "Spotter works out the number. With no match and no number from the person, you may estimate: send "
            "it with estimated true and say so in your reply."
        ),
        annotations=READ_ONLY,
    )
    async def find_food(
        query: Annotated[str, Field(description="What the person ate, in their words, such as banana or roti.")],
        limit: Annotated[int | None, Field(
            description="How many of each kind of match to return. Leave out for 8.")] = None,
    ) -> Annotated[CallToolResult, FindFoodResult]:
        sent = _sent(limit=limit)
        return await wrapper.call("find_food", FindFoodResult,
                                  lambda account: services.find_food(account, query, **sent))

    @server.tool(
        name="log_food",
        title="Log food",
        description=(
            "Records what the person ate, one entry per food, in one call for the whole message. Spotter tags "
            "each number with who supplied it; you never name the tag. " + FOOD_RULE + " " + FOOD_NAME_RULE + " "
            "Returns the entries, their tags and the day's total. The same set again within 10 minutes is a "
            "duplicate: nothing is written."
        ),
        annotations=WRITE,
    )
    async def log_food(
        entries: Annotated[list[FoodEntryIn], Field(description="Every food in the message, one entry each.")],
        date: Annotated[str | None, Field(
            description="The day it was eaten, YYYY-MM-DD, at most 2 years back. Leave out for today.")] = None,
        allow_repeat: Annotated[bool, Field(
            description="true logs it even when it matches food logged in the last 10 minutes. Send it only "
                        "when the person confirms they ate it again.")] = False,
    ) -> Annotated[CallToolResult, LogFoodResult]:
        data = _sent(entries=[x.model_dump(exclude_none=True) for x in entries], date=date,
                     allow_repeat=allow_repeat or None)
        return await wrapper.call("log_food", LogFoodResult,
                                  lambda account: services.log_food(account, data, "claude"))

    @server.tool(
        name="save_food",
        title="Save a food",
        description=(
            "Saves a food the person eats often, with its serving and calories per serving, so it can be logged "
            "later by id and an amount. Saving the same name again updates it."
        ),
        annotations=SETTING,
    )
    async def save_food(
        name: Annotated[str, Field(description="The food's name, as the person says it, such as Roti.")],
        serving_label: Annotated[str, Field(description="What one serving is, such as 1 roti or 1 bowl.")],
        kcal_per_serving: Annotated[float, Field(
            description="Calories in one serving, as the person gave them, 0 to 5,000.")],
        grams_per_serving: Annotated[float | None, Field(description="Grams in one serving, if known.")] = None,
    ) -> Annotated[CallToolResult, SaveFoodResult]:
        data = _sent(name=name, serving_label=serving_label, kcal_per_serving=kcal_per_serving,
                     grams_per_serving=grams_per_serving)
        return await wrapper.call("save_food", SaveFoodResult,
                                  lambda account: services.save_food(account, data, "claude"))

    @server.tool(
        name="log_cardio",
        title="Log cardio",
        description=(
            "Records one cardio session: the activity, as list_exercises gives it (kind cardio), and minutes, "
            "with distance and the machine's numbers when the person gave them. Spotter estimates the calories "
            "from the activity's MET value and the person's current body weight, and shows the machine's "
            "number beside it. With no weigh-in there is no estimate. The same session again within "
            "10 minutes is a duplicate: nothing is written."
        ),
        annotations=WRITE,
    )
    async def log_cardio(
        exercise: Annotated[str, Field(
            description="The activity, exactly as list_exercises gives it, such as Treadmill Walk. " + CARDIO_RULE)],
        minutes: Annotated[float, Field(description="How long it lasted, in minutes, 1 to 600.")],
        date: Annotated[str | None, Field(
            description="The day, YYYY-MM-DD, at most 2 years back. Leave out for today.")] = None,
        distance_km: Annotated[float | None, Field(description="The distance in km, if the person gave it.")] = None,
        incline_pct: Annotated[float | None, Field(description="A treadmill's incline in percent, 0 to 40.")] = None,
        speed_kmh: Annotated[float | None, Field(description="The speed in km/h, if the person gave it.")] = None,
        avg_hr: Annotated[int | None, Field(description="Average heart rate in bpm, 30 to 240.")] = None,
        machine_kcal: Annotated[int | None, Field(
            description="The calories the machine or watch showed, as the person read them.")] = None,
        notes: Annotated[str | None, Field(description="How it went, in the person's words.")] = None,
        allow_repeat: Annotated[bool, Field(
            description="true logs it even when it matches a session from the last 10 minutes.")] = False,
    ) -> Annotated[CallToolResult, LogCardioResult]:
        data = _sent(exercise=exercise, minutes=minutes, date=date, distance_km=distance_km, incline_pct=incline_pct,
                     speed_kmh=speed_kmh, avg_hr=avg_hr, machine_kcal=machine_kcal, notes=notes,
                     allow_repeat=allow_repeat or None)
        return await wrapper.call("log_cardio", LogCardioResult,
                                  lambda account: services.log_cardio(account, data, "claude"))

    @server.tool(
        name="get_trends",
        title="Get trends",
        description=(
            "How the person is doing, from their own logs: the smoothed body weight trend and its rate per week, "
            "calories a day over the last 7 and 28 days, lift sessions and cardio minutes this week against their "
            "targets, and an estimate of their maintenance calories once enough food and weigh-ins are logged, "
            "else what it still needs. " + TRENDS_RULE + " Quote these numbers."
        ),
        annotations=READ_ONLY,
    )
    async def get_trends() -> Annotated[CallToolResult, GetTrendsResult]:
        return await wrapper.call("get_trends", GetTrendsResult, lambda account: services.get_trends(account))

    @server.tool(
        name="get_goals",
        title="Get goals",
        description=(
            "Every open goal of the person's: where it started, where it is now, the target and its date, the pace "
            "it needs per week against the pace so far, the date it is reached at that pace, and its status "
            "(reached, on track, behind or not enough data). " + GOALS_RULE + " Quote these numbers."
        ),
        annotations=READ_ONLY,
    )
    async def get_goals(
        include_closed: Annotated[bool, Field(
            description="true also lists the goals marked hit or dropped. Leave out for open goals only.")] = False,
    ) -> Annotated[CallToolResult, GetGoalsResult]:
        sent = _sent(include_closed=include_closed or None)
        return await wrapper.call("get_goals", GetGoalsResult, lambda account: services.get_goals(account, **sent))

    @server.tool(
        name="import_entries",
        title="Import entries",
        description=IMPORT_DESCRIPTION,
        # A write, as the log functions are: up to 200 rows with no undo, a re-run can write again in the documented
        # cases, and every real call counts against the write limit. Not a setting.
        annotations=WRITE,
    )
    async def import_entries(
        rows: Annotated[list[ImportRowIn], Field(
            description="The rows to bring over, oldest first: 1 to 200 a call, holding at most 1,000 sets and food "
                        "entries together. Keep one day's rows of a kind in the same call. Send the rest in the "
                        "next call.",
            json_schema_extra={"minItems": IMPORT_ROWS[0], "maxItems": IMPORT_ROWS[1]})],
        # Strict: true, false or left out. A loose value ("no", "off", 0) is refused here, never read as false.
        dry_run: Annotated[StrictBool, Field(
            description="true, or left out, checks every row and writes nothing. false writes them. Send false only "
                        "after the person has seen a preview and said yes.")] = not_sent(),
        # No unit has a default: one a row needs and the call left out is the service's missing_unit refusal.
        weight_unit: Annotated[str | None, Field(
            description="kg or lb: the unit of every weight in these rows, a weigh-in's or a set's, as the export "
                        "writes it. Needed when a row holds a weight: a call without it is refused.")] = None,
        distance_unit: Annotated[str | None, Field(
            description="km or mi: the unit of every distance in these rows, as the export writes it, and of every "
                        "speed an hour. Needed when a row holds a distance or a speed: a call without it is "
                        "refused.")] = None,
        length_unit: Annotated[str | None, Field(
            description="cm or in: the unit of every body size (waist, hips, chest, arm, thigh) in these rows. "
                        "Needed when a row holds a body size: a call without it is refused.")] = None,
    ) -> Annotated[CallToolResult, ImportEntriesResult]:
        # The five fields the service takes and nothing else: it refuses any other. A row's fields go as sent.
        data = _sent(rows=[r.model_dump(exclude_none=True) for r in rows], dry_run=dry_run, weight_unit=weight_unit,
                     distance_unit=distance_unit, length_unit=length_unit)
        return await wrapper.call("import_entries", ImportEntriesResult,
                                  lambda account: services.import_entries(account, data, "claude"))

    # Last, so everyone else's list is the nineteen above, in their order (SpotterServer.list_tools).
    @server.tool(
        name=OWNER_FUNCTION,
        title="Owner: calls",
        description=(
            "For Spotter's owner alone: what calls did, from the call log, never what the person sent. With no "
            "person named, one line per account with a call in the window, and one for the calls with no account. "
            "With account_id or email, that person's calls, newest first. With no_account, the calls that had no "
            "account. With request_id, that request's calls. Each call gives when it "
            "started, who called (web or claude), the function, ok and the error, the outcome, the step and line "
            "it stopped at, the field names it sent, notes, its time and size, and its request id."
        ),
        annotations=READ_ONLY,
    )
    async def owner_calls(
        account_id: Annotated[int | None, Field(description="An account's id, as a line gives it.")] = None,
        email: Annotated[str | None, Field(description="An account's email, matched exactly, ignoring case.")] = None,
        request_id: Annotated[str | None, Field(
            description="A request's id, from its X-Request-Id header. Goes before account_id and email.")] = None,
        no_account: Annotated[bool, Field(
            description="true lists the calls that had no account: a refused sign-in, or a deleted account.")] = False,
        hours: Annotated[int | None, Field(
            description="The window, in hours back from now: 1 to 2,160. Leave out for 24.",
            ge=HOURS[0], le=HOURS[1])] = None,
        failed_only: Annotated[bool, Field(description="true keeps only the calls that were not ok.")] = False,
        limit: Annotated[int | None, Field(
            description="The most lines or calls: 1 to 200. Leave out for 50.", ge=LIMIT[0], le=LIMIT[1])] = None,
    ) -> Annotated[CallToolResult, OwnerCallsResult]:
        sent = _sent(account_id=account_id, email=email, request_id=request_id, no_account=no_account or None,
                     hours=hours, failed_only=failed_only or None, limit=limit)
        return await wrapper.call(OWNER_FUNCTION, OwnerCallsResult, lambda _account: services.owner_calls(sent))

    @server.resource(
        CAN_AND_CANT_URI,
        name="can-and-cant",
        title="What Spotter can and cannot tell you",
        description="What Spotter records, what its numbers mean, and what it cannot know.",
        mime_type="text/markdown",
    )
    async def can_and_cant() -> str:
        return await _resource(functools.partial(CAN_AND_CANT.read_text, encoding="utf-8"))

    @server.resource(
        EXERCISES_URI,
        name="exercises",
        title="The exercise list",
        description="Every exercise name Spotter knows, as JSON: the same as list_exercises with no filter.",
        mime_type="application/json",
    )
    async def exercise_list() -> str:
        return await _resource(lambda: json.dumps(services.list_exercises(), separators=(",", ":")))

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    if registered != set(NEEDS):  # a function without a level would be refused; fail at start, not per call
        raise RuntimeError(f"NEEDS does not match the functions: {sorted(registered ^ set(NEEDS))}")
    return server


async def _resource(read: Callable[[], str]) -> str:
    """Read a resource in a worker thread. A failure is logged by type and frames, never its message."""
    try:
        return await anyio.to_thread.run_sync(read)
    except DATABASE_DOWN as exc:
        log.warning("MCP resource could not use the database: %s", type(exc).__name__)
        raise ResourceError("Spotter cannot reach its database right now. Retry in a minute.") from None
    except Exception as exc:  # noqa: BLE001
        log.error("MCP resource crashed: %s", calllog.crash_text(exc))
        raise ResourceError("Spotter could not read this resource.") from None

