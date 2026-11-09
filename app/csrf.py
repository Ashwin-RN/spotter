"""Cross-site request forgery protection for the web pages, written by hand.

Each session gets one random token. Every POST form carries it in a hidden field named `csrf_token`,
and every non-GET web route compares the field with the session's copy before doing anything else.
A missing or wrong token gets 403. /mcp is not covered: it is called with bearer tokens, not cookies.

    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
"""

import secrets
from collections.abc import Awaitable, Callable
from functools import wraps

from starlette.requests import Request
from starlette.responses import Response

FIELD = "csrf_token"
SESSION_KEY = "csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

Endpoint = Callable[[Request], Awaitable[Response]]


def token(request: Request) -> str:
    """This session's token, made on first use."""
    current = request.session.get(SESSION_KEY)
    if not isinstance(current, str) or not current:
        current = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = current
    return current


def same(sent: object, expected: object) -> bool:
    """Constant-time equality for two non-empty strings. Anything else is unequal (never an error)."""
    if not isinstance(sent, str) or not isinstance(expected, str) or not sent or not expected:
        return False
    return secrets.compare_digest(sent.encode("utf-8"), expected.encode("utf-8"))


async def valid(request: Request) -> bool:
    """True when the posted form's token matches this session's."""
    if "session" not in request.scope:
        return False
    form = await request.form()  # Starlette keeps the parsed form on the request for the endpoint
    return same(form.get(FIELD), request.session.get(SESSION_KEY))


def protect(endpoint: Endpoint, refuse: Callable[[Request], Response]) -> Endpoint:
    """Wrap a web endpoint so every non-GET request is checked first; `refuse` builds the 403."""

    @wraps(endpoint)
    async def checked(request: Request) -> Response:
        if request.method not in SAFE_METHODS and not await valid(request):
            return refuse(request)
        return await endpoint(request)

    return checked
