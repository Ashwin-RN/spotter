"""The Spotter server: /health, the web pages and the MCP endpoint at /mcp.

The app is built by create_app(settings), which first makes those settings the process-wide ones (the
database, the user lookup and the link helper all read them). The two halves fail closed on their own:
- Without a usable AUTHKIT_DOMAIN and MCP_RESOURCE_URL, /mcp returns 503 and no token is accepted,
  and the web pages say sign-in is not set up. /health still answers.
- Without the WorkOS values, the web pages say sign-in is not set up and MCP still works.

The session cookie is attached only to the web routes, so /mcp and /.well-known/ never read or set it.
Last of all, web.fallback at "/": /mcp, /.well-known and anything under them go to the MCP app as before,
and every other path no route answered gets the designed 404 page with the page header.

Around the whole app, BodyLimit: a request body over MAX_BODY_BYTES is answered 413 before any route reads
it. The largest real body, an import_entries call of 200 rows, is far under it.

Outside everything, Starlette's own error layer included, RequestId (SpotterApp): every request gets one id of
Spotter's own making, held in calllog.REQUEST_ID while it runs, written on its call-log rows and its log lines,
and sent back as X-Request-Id on every response, a 413 and an unhandled 500 too.
"""

import contextlib
import logging
import secrets

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route, Router
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import calllog, config, web
from app.auth import FetchJson, JwksTokenVerifier, UserinfoClient, fetch_json
from app.config import Settings, load_settings
from app.mcp_server import build_server

ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
MAX_BODY_BYTES = 1024 * 1024  # 1 MiB
TOO_LARGE = "That request is larger than Spotter takes (1 MiB)."


class RequestId:
    """Gives every request one id, 16 hex characters from secrets, never read from the request. It is held in
    calllog.REQUEST_ID for the length of the request and added to the response's headers as X-Request-Id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(8)
        held = calllog.REQUEST_ID.set(request_id)

        async def tagged(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", ()), (b"x-request-id", request_id.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, tagged)
        finally:
            calllog.REQUEST_ID.reset(held)


class SpotterApp(Starlette):
    """Starlette with RequestId outside its whole stack, so the id is on every response: Starlette's own
    ServerErrorMiddleware sits outside every listed middleware, and its 500 would carry none."""

    def build_middleware_stack(self) -> ASGIApp:
        return RequestId(super().build_middleware_stack())


class _BodyTooLarge(Exception):
    """Raised into the app when the streamed body passes the limit, after the 413 has been sent."""


class BodyLimit:
    """Refuses a request body over MAX_BODY_BYTES with a plain 413, before the app reads it.

    A Content-Length over the limit is answered at once and the app never runs. With no length (a chunked
    body), or one that understates, the body is counted as the app receives it: the moment it passes the
    limit, the 413 is sent, the app's own receive raises, and anything the app still tries to send is
    dropped, so the 413 is the only answer. A body cut short there can make the app fail; that failure is
    the refused request's, and is not raised again. If the app had already started its answer, nothing can
    replace it, and the receive simply raises.
    """

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.limit:
            await _too_large(send)
            return
        received = 0
        started = refused = False

        async def counted() -> Message:
            nonlocal received, refused
            if refused:  # an app that caught the refusal and reads on hears the client has gone
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    if not started:
                        refused = True
                        await _too_large(send)
                    raise _BodyTooLarge
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            if refused:
                return  # the 413 has been sent: the app's own answer goes nowhere
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except Exception:
            if not refused:
                raise


def _content_length(scope: Scope) -> int | None:
    """The request's Content-Length as a number, or None when it has none or it is not a number (the server
    refuses a malformed one itself)."""
    for name, value in scope.get("headers") or ():
        if name.lower() == b"content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isascii() and text.isdigit() else None
    return None


async def _too_large(send: Send) -> None:
    body = TOO_LARGE.encode("utf-8")
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})


class _NoQueryStrings(logging.Filter):
    """Cuts the query string off the server's access-log lines: the sign-in callback's carries a
    one-time code and the state, and no log line may hold either."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


class _RequestIds(logging.Filter):
    """Gives each of Spotter's log lines the request's id, or - outside a request."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = calllog.REQUEST_ID.get() or "-"
        return True


def _set_up_logging() -> None:
    """Spotter's own log lines go to stderr through their own handler, added once, each with its request's id.
    INFO lines are only ever written when AUTH_DEBUG is on; warnings always are. The access log never shows a
    query string."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _NoQueryStrings) for f in access.filters):
        access.addFilter(_NoQueryStrings())
    logger = logging.getLogger("spotter")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s [%(request_id)s]: %(message)s"))
        handler.addFilter(_RequestIds())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # not printed twice when the root logger also has a handler


async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


def _not_configured(problem: str):
    async def handler(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "error": "not_configured",
                "message": f"Spotter cannot check sign-ins yet. {problem}",
                "fix": "Set AUTHKIT_DOMAIN (the provider's host) and MCP_RESOURCE_URL (this server's /mcp "
                       "address, exactly as registered with the provider), then restart.",
            },
            status_code=503,
        )

    return handler


def create_app(settings: Settings, fetch: FetchJson = fetch_json) -> Starlette:
    """Build the app. `fetch` is how the server reaches the provider; tests pass their own."""
    config.use_settings(settings)
    _set_up_logging()
    web_problem = web.problem(settings)
    if web_problem is not None:
        logging.getLogger("spotter").warning("Web sign-in disabled: %s", web_problem)

    problem = settings.problem
    if problem is not None:
        logging.getLogger("spotter").warning("MCP endpoint disabled: %s", problem)
        not_configured = _not_configured(problem)
        # What the fallback hands /mcp/... and /.well-known/... to: they answer as they did with no mount at
        # "/", a redirect from /mcp/ to /mcp and a plain 404 for the rest.
        mcp_off = Router(routes=[Route("/mcp", not_configured, methods=ALL_METHODS)])
        return SpotterApp(routes=[
            Route("/health", health),
            *web.routes(settings),
            Route("/mcp", not_configured, methods=ALL_METHODS),
            Mount("/", app=web.fallback(settings, mcp_off)),
        ], middleware=[Middleware(BodyLimit)])

    server = build_server(settings, JwksTokenVerifier(settings, fetch), UserinfoClient(settings, fetch))
    mcp_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,  # every call stands alone
        json_response=True,
        # Off on purpose: with it on, calls through a public address get "421 Invalid Host header".
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():  # without this every MCP call hangs
            yield

    return SpotterApp(
        routes=[
            Route("/health", health),
            *web.routes(settings),
            # Last: the MCP app answers /mcp and the protected-resource metadata under /.well-known/; every
            # other path no route answered gets the 404 page.
            Mount("/", app=web.fallback(settings, mcp_app)),
        ],
        middleware=[Middleware(BodyLimit)],  # inside RequestId (SpotterApp), before any route reads
        lifespan=lifespan,
    )


app = create_app(load_settings())
