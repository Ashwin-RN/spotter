"""The provider seam for web sign-in. Tests replace these functions; nothing else in Spotter talks to
the provider's library.

    sign_in_url(state)       -> the provider's sign-in address, carrying `state`
    exchange(code)           -> Identity(subject, email, name, session_id)  (async, time-limited)
    sign_out_url(session_id) -> the provider's sign-out address, returning to the home page

Only the person's provider id, email, name and provider session id leave this module. The access
token and refresh token the provider returns are dropped here and never stored. The provider client
is built on use, never at import, from the running settings.
"""

import asyncio
import logging
from dataclasses import dataclass

import jwt
from workos import AsyncWorkOSClient, WorkOSClient

from app import links
from app.config import get_settings

log = logging.getLogger("spotter")

PROVIDER_API = "https://api.workos.com"
REQUEST_SECONDS = 10  # one provider call
EXCHANGE_SECONDS = 15  # the whole exchange, whatever the library does inside it


class SignInFailed(Exception):
    """The provider did not finish the sign-in. The reason is logged by type only."""


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str | None
    name: str | None
    session_id: str | None = None


def callback_url() -> str:
    """Where the provider sends the person back: <site origin>/auth/callback (registered with it)."""
    origin = get_settings().site_origin
    if origin is None:
        raise RuntimeError("Web sign-in needs MCP_RESOURCE_URL: it gives the site's address.")
    return origin + "/auth/callback"


def _client_values() -> dict:
    """Every value is passed in, so the library never falls back to reading the environment."""
    settings = get_settings()
    return {"api_key": settings.workos_api_key, "client_id": settings.workos_client_id,
            "base_url": PROVIDER_API, "request_timeout": REQUEST_SECONDS, "max_retries": 0}


def sign_in_url(state: str) -> str:
    """The provider's hosted sign-in page for this attempt. Builds an address; no network call."""
    with WorkOSClient(**_client_values()) as client:
        return client.user_management.get_authorization_url(
            provider="authkit", redirect_uri=callback_url(), state=state)


def sign_out_url(session_id: str) -> str:
    """The provider's sign-out address for this provider session, coming back to the home page.
    Builds an address; no network call."""
    with WorkOSClient(**_client_values()) as client:
        return client.user_management.get_logout_url(session_id=session_id, return_to=links.page("home"))


def _session_id(access_token: str) -> str | None:
    """The provider session id (`sid`) from the access token the provider just returned to us over TLS.
    Read only to build the sign-out address; the token itself is discarded."""
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    sid = claims.get("sid")
    return sid if isinstance(sid, str) and sid else None


def _name(user) -> str | None:
    name = getattr(user, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    parts = [p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None))
             if isinstance(p, str) and p.strip()]
    return " ".join(p.strip() for p in parts) or None


async def _authenticate(code: str) -> Identity:
    async with AsyncWorkOSClient(**_client_values()) as client:
        result = await client.user_management.authenticate_with_code(code=code)
    user = result.user
    return Identity(subject=user.id, email=user.email, name=_name(user),
                    session_id=_session_id(result.access_token))


async def exchange(code: str) -> Identity:
    """Finish a sign-in: trade the one-time code for the person's identity. Any failure, including a
    slow provider, raises SignInFailed. Never blocks the event loop."""
    try:
        return await asyncio.wait_for(_authenticate(code), timeout=EXCHANGE_SECONDS)
    except Exception as exc:  # the provider's errors, the network's, a timeout, a malformed reply
        log.warning("Web sign-in failed at the provider: %s", type(exc).__name__)
        raise SignInFailed(type(exc).__name__) from None
