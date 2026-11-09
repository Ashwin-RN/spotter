"""The Spotter server: /health and the MCP endpoint at /mcp.

The app is built by create_app(settings). Without a usable AUTHKIT_DOMAIN and MCP_RESOURCE_URL it still
starts and /health still answers, but /mcp returns 503 and no token is accepted: it fails closed.
"""

import contextlib
import logging

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from app.auth import FetchJson, JwksTokenVerifier, UserinfoClient, fetch_json
from app.config import Settings, load_settings
from app.mcp_server import build_server

ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _set_up_logging() -> None:
    """Spotter's own log lines go to stderr through their own handler, added once. INFO lines are only
    ever written when AUTH_DEBUG is on; warnings always are."""
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
    _set_up_logging()
    problem = settings.problem
    if problem is not None:
        logging.getLogger("spotter").warning("MCP endpoint disabled: %s", problem)
        return Starlette(routes=[
            Route("/health", health),
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
            # Last: answers /mcp and the protected-resource metadata under /.well-known/.
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )


app = create_app(load_settings())
