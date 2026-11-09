"""Spotter's MCP server: six functions and two resources.

The MCP library does the sign-in work: it serves the protected-resource metadata, answers 401 with
`resource_metadata`, and runs every call through the token verifier. Functions read the verified token
the library stored for this request; nothing here reads the Authorization header.

Scopes: the metadata advertises SCOPES, and a client asks the provider for what is advertised there
(the library's challenges carry no `scope=` of their own). The server requires only REQUIRED_SCOPES.

One wrapper runs every function (Server.call below). In order: it notes the start time, reads the
verified token, finds or creates the person's account (users.user_for_identity), asks the provider's
userinfo for a missing email or name (at most once per LOOKUP_EVERY seconds per person), runs the
service with the account id, turns a crash into {error: internal_error, ...}, and writes one call-log
row. The services and the user lookup are synchronous and use the database, so each runs in a worker
thread, never on the event loop.

Every result is the service's own dict, as structured content and as compact JSON text. A service
error ({error, message, fix}) is flagged is_error. A duplicate is a success.
"""

import functools
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlparse

import anyio.to_thread
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.applications import Starlette
from starlette.routing import Route

from app import calllog, db, services, users
from app.auth import JwksTokenVerifier, UserinfoClient
from app.config import ROOT, Settings
from app.mcp_schemas import (
    ExercisesResult, GetProgressResult, LogBodyweightResult, LogLiftsResult, SetGoalResult, SetIn, WhoamiResult,
)
from app.models import User

log = logging.getLogger("spotter.mcp")

INSTRUCTIONS = (
    "Spotter is the person's own record of lifts, body weight and goals. "
    "Call whoami first: it says whose account this is and what today's date is for them. "
    "If whoami carries time_zone_note, ask the person their time zone and point them to the Settings link. "
    "Use exercise names exactly as list_exercises gives them. "
    "Leave the date out for something done today: today is the person's own date. "
    "Quote Spotter's numbers; never estimate your own. "
    "If a call returns an error, read its fix and retry. "
    "duplicate: true means nothing was written: say so, and send allow_repeat: true only when the person "
    "confirms it was a separate entry. "
    "Put links last."
)

# What clients are told to ask for. offline_access is what gets a client a refresh token.
SCOPES = ["openid", "profile", "email", "offline_access"]
# What a token must carry to get in: openid, without which the provider's userinfo cannot name the person.
REQUIRED_SCOPES = ["openid"]

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Nothing is hard-deleted: a replaced weigh-in is kept and can be restored. So no write is destructive.
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

CAN_AND_CANT = ROOT / "docs" / "can-and-cant.md"
CAN_AND_CANT_URI = "spotter://can-and-cant"
EXERCISES_URI = "spotter://exercises"

# Only these mean "the database is not there" (or too busy to answer: every pooled connection is in use).
# Any other error is a crash.
DATABASE_DOWN = (OperationalError, InterfaceError, PoolTimeout, db.DatabaseNotConfigured)

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
        account_id = None
        try:
            token = get_access_token()
            user = await self._account(token)
            if isinstance(user, dict):
                result = user
            else:
                account_id = user.id
                result = await anyio.to_thread.run_sync(work, account_id)
                if add is not None and "error" not in result:
                    result = add(result, token)
            is_error = "error" in result
            if not is_error:
                model.model_validate(result)  # a result that breaks its schema is Spotter's bug, not the caller's
            # Raises on anything that cannot be sent, such as a lone surrogate: then the answer is internal_error.
            text, size = _json_text(result)
        except DATABASE_DOWN as exc:
            log.warning("MCP %s could not use the database: %s", function, type(exc).__name__)
            result, is_error = database_unavailable(), True
            text, size = _json_text(result)
        except Exception as exc:  # noqa: BLE001 - the caller gets a typed error; the log gets type and frames
            log.error("MCP %s crashed: %s", function, calllog.crash_text(exc))
            result, is_error = internal_error(function), True
            text, size = _json_text(result)

        ms = int((time.monotonic() - start) * 1000)
        try:
            await anyio.to_thread.run_sync(
                calllog.write, function, started_at, ms, not is_error, result.get("error") if is_error else None,
                size, account_id)
        except Exception as exc:  # noqa: BLE001 - a lost log row never costs the person their answer
            log.warning("MCP %s: the call log could not be written: %s", function, type(exc).__name__)
        return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=result,
                              is_error=is_error)


def _sign_in(result: dict, token: AccessToken) -> dict:
    """whoami's result plus the sign-in block, from the verified token."""
    claims = token.claims or {}
    return {**result, "sign_in": {"client_id": token.client_id, "scopes": token.scopes,
                                  "issuer": _claim(claims, "iss") or "", "expires_at": token.expires_at or 0}}


def _sent(**values) -> dict:
    """The arguments the caller sent, as the service's data dict. Left out: None, so service defaults apply."""
    return {k: v for k, v in values.items() if v is not None}


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
            "Call it first. If it carries time_zone_note, ask the person their time zone."
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
            description="squat, hinge, push, pull, carry, core or accessory. Leave out for all.")] = None,
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
            "Sets a lift goal (an estimated one-rep max to reach) or a body weight goal, with a target and a "
            "date. Spotter stores where it starts and which way it points. With goal_id, changes one of the "
            "person's goals instead: its target, date, text, or status (hit, dropped, open)."
        ),
        annotations=WRITE,
    )
    async def set_goal(
        kind: Annotated[str | None, Field(
            description="lift or bodyweight. Needed for a new goal; leave out with goal_id.")] = None,
        exercise: Annotated[str | None, Field(
            description="For a lift goal: the exercise name as list_exercises gives it.")] = None,
        target_value: Annotated[float | None, Field(
            description="The target in kg. A lift: an estimated one-rep max, above 0 to 666.67. "
                        "Body weight: 20 to 400.")] = None,
        target_date: Annotated[str | None, Field(
            description="The date to reach it by, YYYY-MM-DD, after today.")] = None,
        text: Annotated[str | None, Field(
            description="The goal in the person's words. Up to 2,000 characters.")] = None,
        start_value: Annotated[float | None, Field(
            description="Where the person is now, in kg. Used only when nothing is logged yet to start from.")] = None,
        goal_id: Annotated[int | None, Field(
            description="To change a goal: its id, from set_goal or get_progress. Leave out for a new goal.")] = None,
        status: Annotated[str | None, Field(
            description="With goal_id: hit (reached), dropped, or open (reopen).")] = None,
    ) -> Annotated[CallToolResult, SetGoalResult]:
        data = _sent(kind=kind, exercise=exercise, target_value=target_value, target_date=target_date, text=text,
                     start_value=start_value, goal_id=goal_id, status=status)
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
            description="The lift, as list_exercises gives it. Leave out for every lift.")] = None,
    ) -> Annotated[CallToolResult, GetProgressResult]:
        return await wrapper.call("get_progress", GetProgressResult,
                                  lambda account: services.get_progress(account, exercise))

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

