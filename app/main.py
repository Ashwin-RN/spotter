"""The Spotter server: /health, the web pages and the MCP endpoint at /mcp.

The app is built by create_app(settings), which first makes those settings the process-wide ones (the
database, the user lookup and the link helper all read them). The two halves fail closed on their own:
- Without a usable AUTHKIT_DOMAIN and MCP_RESOURCE_URL, /mcp returns 503 and no token is accepted,
  and the web pages say sign-in is not set up. /health still answers.
- Without the WorkOS values, the web pages say sign-in is not set up and MCP still works.

The session cookie is attached only to the web routes, so /mcp and /.well-known/ never read or set it.
"""

import contextlib
import logging

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from app import config, web
from app.auth import FetchJson, JwksTokenVerifier, UserinfoClient, fetch_json
from app.config import Settings, load_settings
from app.mcp_server import build_server

ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


class _NoQueryStrings(logging.Filter):
    """Cuts the query string off the server's access-log lines: the sign-in callback's carries a
    one-time code and the state, and no log line may hold either."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def _set_up_logging() -> None:
    """Spotter's own log lines go to stderr through their own handler, added once. INFO lines are only
    ever written when AUTH_DEBUG is on; warnings always are. The access log never shows a query string."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _NoQueryStrings) for f in access.filters):
        access.addFilter(_NoQueryStrings())
    logger = logging.getLogger("spotter")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
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
        return Starlette(routes=[
            Route("/health", health),
            *web.routes(settings),
            Route("/mcp", _not_configured(problem), methods=ALL_METHODS),
        ])

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

    return Starlette(
        routes=[
            Route("/health", health),
            *web.routes(settings),
            # Last: answers /mcp and the protected-resource metadata under /.well-known/.
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )


app = create_app(load_settings())
