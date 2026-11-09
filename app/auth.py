"""The token verifier, and the one outbound lookup made with a signed-in person's token.

Spotter is a resource server: the provider (WorkOS AuthKit) signs access tokens, Spotter checks them.
A token is accepted only when it is an RS256 JWT signed by a key in the provider's key set, its `iss`
is the provider exactly, its `aud` contains this server's resource address exactly, it has a `sub`,
and it has not expired.

Outbound fetches never follow a redirect, so a token sent to the provider cannot be forwarded elsewhere.
Nothing here ever logs a token or an Authorization header.
"""

import asyncio
import functools
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import anyio
import anyio.to_thread
import jwt
from mcp.server.auth.provider import AccessToken

from app.config import Settings

log = logging.getLogger("spotter.auth")

FetchJson = Callable[..., Awaitable[dict[str, Any]]]  # (url, headers=None) -> parsed JSON object

KEY_SET_TTL = 3600.0  # seconds a fetched key set is fresh; after this it is refreshed behind the request
KEY_SET_MAX_AGE = 24 * 3600.0  # seconds after which an old key set is no longer trusted at all
REFRESH_INTERVAL = 60.0  # after a successful fetch, the key set is fetched again at most this often
RETRY_AFTER_FAILURE = 5.0  # after a failed fetch, the next attempt may come this soon
USERINFO_ENDPOINT_TTL = 3600.0  # seconds the provider's userinfo address (or its absence) is remembered
USERINFO_TIMEOUT = 6.0  # the whole userinfo lookup, both fetches together
USERINFO_THREADS = 4  # userinfo and discovery fetch threads alive at once, abandoned ones included
FETCH_TIMEOUT = 5.0  # per socket operation
MAX_BODY = 64 * 1024  # bytes; the discovery documents and the key set are far smaller
CLOCK_SKEW = 30  # seconds of tolerance on iat and nbf only; expiry gets none
LOG_VALUE_LIMIT = 200  # characters of any one claim value written to the log
USER_AGENT = "Spotter/0.1"  # a named agent: some CDN-fronted hosts refuse the default Python-urllib one


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect is an error. Following one would carry the Authorization header to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def build_opener(*handlers: Any) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_RefuseRedirects, *handlers)


_OPENER = build_opener()


def _get_json(url: str, headers: dict[str, str] | None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                                                   **(headers or {})})
    # Callers pass only https addresses on the provider's host, and the opener refuses redirects.
    with _OPENER.open(request, timeout=FETCH_TIMEOUT) as response:  # noqa: S310
        raw = response.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError("response too large")
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    return body


class Busy(Exception):
    """Every slot is held by a fetch thread that has not finished yet."""


class _Slot:
    """One place in a pool, taken before the thread starts and given back when the thread itself ends
    (or at once, if the thread never started)."""

    def __init__(self, pool: threading.BoundedSemaphore):
        self._pool = pool
        self._guard = threading.Lock()
        self._started = False
        self._released = False

    def start(self) -> bool:
        with self._guard:
            self._started = not self._released
            return self._started

    def release(self) -> None:
        with self._guard:
            if not self._released:
                self._released = True
                self._pool.release()

    def release_if_never_started(self) -> None:
        with self._guard:
            started = self._started
        if not started:
            self.release()


async def fetch_json(url: str, headers: dict[str, str] | None = None, *,
                     slots: threading.BoundedSemaphore | None = None) -> dict[str, Any]:
    """GET a JSON object in a worker thread, so the event loop never waits on the network.

    A cancelled caller does not wait for the thread: it is abandoned and finishes on its own. With `slots`,
    the thread holds one slot until it actually ends, and when none is free this raises Busy at once."""
    if slots is None:
        return await anyio.to_thread.run_sync(_get_json, url, headers, abandon_on_cancel=True)
    if not slots.acquire(blocking=False):
        raise Busy("too many provider fetches still running")
    slot = _Slot(slots)

    def work() -> dict[str, Any]:
        if not slot.start():
            raise Busy("cancelled before it started")
        try:
            return _get_json(url, headers)
        finally:
            slot.release()

    try:
        return await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
    except BaseException:
        slot.release_if_never_started()
        raise


def _scopes(claims: dict[str, Any]) -> list[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        return scope.split()
    scp = claims.get("scp")
    if isinstance(scp, list):
        return [str(s) for s in scp]
    return []


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= LOG_VALUE_LIMIT else text[:LOG_VALUE_LIMIT] + "...(cut)"


class JwksTokenVerifier:
    """Checks access tokens against the provider's key set. Implements the MCP library's TokenVerifier."""

    def __init__(self, settings: Settings, fetch: FetchJson = fetch_json, clock: Callable[[], float] = time.monotonic):
        self.issuer = settings.issuer
        self.audience = settings.mcp_resource_url
        self.jwks_url = settings.jwks_url
        self.debug = settings.auth_debug
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None  # last successful fetch
        self._last_attempt: float | None = None  # last fetch attempt, successful or not
        self._last_failed = False
        self._inflight: asyncio.Task | None = None  # the one key set fetch in flight, if any

    def _age(self, now: float) -> float:
        return float("inf") if self._fetched_at is None else now - self._fetched_at

    def _start_refresh(self, now: float) -> asyncio.Task | None:
        """The refresh in flight, or a new one if the rate limit allows it, or None."""
        if self._inflight is not None and not self._inflight.done():
            return self._inflight
        interval = RETRY_AFTER_FAILURE if self._last_failed else REFRESH_INTERVAL
        if self._last_attempt is not None and now - self._last_attempt < interval:
            return None
        self._last_attempt = now
        self._inflight = asyncio.get_running_loop().create_task(self._refresh())
        return self._inflight

    async def _refresh(self) -> None:
        try:
            body = await self._fetch(self.jwks_url)
        except Exception as exc:  # noqa: BLE001 - a failed fetch keeps the old keys and refuses unknown ones
            self._last_failed = True
            log.warning("key set fetch failed: %s", type(exc).__name__)
            return
        keys: dict[str, Any] = {}
        for jwk in body.get("keys", []) if isinstance(body.get("keys"), list) else []:
            if not isinstance(jwk, dict) or jwk.get("kty") != "RSA" or not jwk.get("kid"):
                continue
            if jwk.get("use") not in (None, "sig") or jwk.get("alg") not in (None, "RS256"):
                continue
            try:
                keys[str(jwk["kid"])] = jwt.PyJWK(jwk, algorithm="RS256").key
            except jwt.PyJWTError:
                continue
        self._keys = keys
        self._fetched_at = self._clock()
        self._last_failed = False

    async def _key_for(self, kid: str) -> Any | None:
        """The key for `kid`.

        A known key is returned at once from a set under a day old; a set over an hour old is refreshed
        behind the request. An unknown key id (or no usable set) waits for a refresh, joining the one in
        flight if there is one. Fetches are at most once a minute (every few seconds after a failure),
        so a stream of made-up key ids cannot flood the provider."""
        now = self._clock()
        age = self._age(now)
        if kid in self._keys and age < KEY_SET_MAX_AGE:
            if age >= KEY_SET_TTL:
                self._start_refresh(now)  # stale-while-revalidate: this request does not wait for it
            return self._keys[kid]
        task = self._start_refresh(now)
        if task is not None:
            await asyncio.shield(task)  # a cancelled caller leaves the refresh running for the others
        return self._keys.get(kid) if self._age(self._clock()) < KEY_SET_MAX_AGE else None

    def _refused(self, reason: str, token: str) -> None:
        if not self.debug:
            return None
        try:  # unverified, for diagnosis only: the two public claims that most often explain a refusal
            claims = jwt.decode(token, options={"verify_signature": False})
            log.info("token refused: %s (iss=%s aud=%s)", reason, _short(claims.get("iss")), _short(claims.get("aud")))
        except (jwt.PyJWTError, ValueError, TypeError):
            log.info("token refused: %s", reason)
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
        except (jwt.PyJWTError, ValueError, TypeError):
            return self._refused("not a JWT", token)
        if header.get("alg") != "RS256":  # the header never chooses the algorithm
            return self._refused("algorithm is not RS256", token)
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            return self._refused("no key id", token)
        key = await self._key_for(kid)
        if key is None:
            return self._refused("unknown key id", token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
                leeway=CLOCK_SKEW,  # for iat and nbf; expiry is checked strictly below
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            return self._refused(type(exc).__name__, token)
        exp = claims["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):  # PyJWT accepts "1790000000"
            return self._refused("exp is not a number", token)
        if exp <= time.time():
            return self._refused("expired", token)
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            return self._refused("empty sub", token)

        if self.debug:
            log.info(
                "token verified: claims=%s iss=%s aud=%s scope=%s client_id=%s",
                _short(sorted(claims)), _short(claims.get("iss")), _short(claims.get("aud")),
                _short(claims.get("scope")), _short(claims.get("client_id")),
            )
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or ""),
            scopes=_scopes(claims),
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=claims["sub"],
            claims=claims,
        )


class UserinfoClient:
    """Asks the provider who a token belongs to, when the token itself does not say.

    The address comes from the provider's OpenID discovery document (then its OAuth metadata as a
    fallback). The token is only ever sent to the provider's own host, over https, with no redirects.
    The answer is used only when its `sub` is the token's. Any failure gives (None, None).
    """

    def __init__(self, settings: Settings, fetch: FetchJson = fetch_json, clock: Callable[[], float] = time.monotonic,
                 timeout: float = USERINFO_TIMEOUT):
        self.domain = (settings.authkit_domain or "").lower()
        self.discovery_urls = [settings.openid_configuration_url, settings.provider_metadata_url]
        self._slots = threading.BoundedSemaphore(USERINFO_THREADS)
        # The real fetch runs in threads that outlive a timed-out lookup; count them, so abandoned ones
        # cannot pile up. A fetch passed in by a test has no threads to count.
        self._fetch = functools.partial(fetch_json, slots=self._slots) if fetch is fetch_json else fetch
        self._clock = clock
        self._timeout = timeout
        self._endpoint: str | None = None
        self._endpoint_at: float | None = None  # when the endpoint, or its absence, was last learned

    def _on_provider_host(self, endpoint: str) -> bool:
        parsed = urlparse(endpoint)
        try:
            port = parsed.port
        except ValueError:
            return False
        return parsed.scheme == "https" and (parsed.hostname or "").lower() == self.domain and port in (None, 443)

    async def _userinfo_endpoint(self) -> str | None:
        now = self._clock()
        if self._endpoint_at is not None and now - self._endpoint_at < USERINFO_ENDPOINT_TTL:
            return self._endpoint
        endpoint, openid_answered = None, False
        for index, url in enumerate(self.discovery_urls):
            try:
                document = await self._fetch(url)
            except Exception as exc:  # noqa: BLE001 - try the next document
                log.warning("provider discovery fetch failed: %s", type(exc).__name__)
                continue
            openid_answered = openid_answered or index == 0
            if isinstance(document.get("userinfo_endpoint"), str):
                endpoint = document["userinfo_endpoint"]
                break
        if endpoint is not None and not self._on_provider_host(endpoint):
            log.warning("userinfo_endpoint is not on the provider's host; not calling it")
            endpoint = None
        # Remember an address, or the OpenID document's own word that there is none. After a failed
        # OpenID fetch, "none" is not remembered: the fallback document lists no address on this provider.
        if endpoint is not None or openid_answered:
            self._endpoint, self._endpoint_at = endpoint, now
        return endpoint

    async def lookup(self, token: str, subject: str) -> tuple[str | None, str | None]:
        try:
            with anyio.fail_after(self._timeout):
                endpoint = await self._userinfo_endpoint()
                if endpoint is None:
                    return None, None
                info = await self._fetch(endpoint, headers={"Authorization": f"Bearer {token}"})
        except Exception as exc:  # noqa: BLE001 - a lookup failure only means the name is unknown
            log.warning("userinfo lookup failed: %s", type(exc).__name__)
            return None, None
        if info.get("sub") != subject:  # an answer about someone else is no answer
            log.warning("userinfo answered for a different subject; ignoring it")
            return None, None
        email = info.get("email") if isinstance(info.get("email"), str) else None
        name = info.get("name") if isinstance(info.get("name"), str) else None
        if name is None:
            parts = [info.get(k) for k in ("given_name", "family_name") if isinstance(info.get(k), str)]
            name = " ".join(parts) or None
        return email, name
