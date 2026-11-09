"""The token verifier, and the one outbound lookup made with a signed-in person's token.

Spotter is a resource server: the provider (WorkOS AuthKit) signs access tokens, Spotter checks them.
A token is accepted only when it is an RS256 JWT signed by a key in the provider's key set, its `iss`
is the provider exactly, its `aud` contains this server's resource address exactly, and it has not expired.

Nothing here ever logs a token or an Authorization header.
"""

import json
import logging
import time
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

KEY_SET_TTL = 3600.0  # seconds a fetched key set is trusted
REFRESH_INTERVAL = 60.0  # an unknown key id refreshes the key set at most this often
FETCH_TIMEOUT = 5.0
USER_AGENT = "Spotter/0.1"
CLOCK_SKEW = 30  # seconds


def _get_json(url: str, headers: dict[str, str] | None) -> dict[str, Any]:
    # A named agent: some CDN-fronted hosts refuse the default Python-urllib one.
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                                                   **(headers or {})})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 - https only, see callers
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    return body


async def fetch_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """GET a JSON object in a worker thread, so the event loop never waits on the network."""
    return await anyio.to_thread.run_sync(_get_json, url, headers)


def _scopes(claims: dict[str, Any]) -> list[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        return scope.split()
    scp = claims.get("scp")
    if isinstance(scp, list):
        return [str(s) for s in scp]
    return []


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
        self._lock = anyio.Lock()

    async def _refresh(self) -> None:
        self._last_attempt = self._clock()
        try:
            body = await self._fetch(self.jwks_url)
        except Exception as exc:  # noqa: BLE001 - a failed fetch keeps the old keys and refuses unknown ones
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

    async def _key_for(self, kid: str) -> Any | None:
        """The key for `kid`. The key set is fetched when it is older than an hour or lacks `kid`,
        but never more than once a minute, so a stream of made-up key ids cannot flood the provider."""
        async with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= KEY_SET_TTL
            may_fetch = self._last_attempt is None or now - self._last_attempt >= REFRESH_INTERVAL
            if (stale or kid not in self._keys) and may_fetch:
                await self._refresh()
            return self._keys.get(kid)

    def _refused(self, reason: str, token: str) -> None:
        if not self.debug:
            return None
        try:  # unverified, for diagnosis only: the two public claims that most often explain a refusal
            claims = jwt.decode(token, options={"verify_signature": False})
            log.info("token refused: %s (iss=%r aud=%r)", reason, claims.get("iss"), claims.get("aud"))
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
                # Clock skew on iat and nbf. Expiry stays strict: the library refuses exp < now with no leeway.
                leeway=CLOCK_SKEW,
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            return self._refused(type(exc).__name__, token)

        if self.debug:
            log.info(
                "token verified: claims=%s iss=%r aud=%r scope=%r client_id=%r",
                sorted(claims), claims.get("iss"), claims.get("aud"), claims.get("scope"), claims.get("client_id"),
            )
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or ""),
            scopes=_scopes(claims),
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(claims["sub"]),
            claims=claims,
        )


class UserinfoClient:
    """Asks the provider who a token belongs to, when the token itself does not say.

    The token is only ever sent to the provider's own host. Any failure gives (None, None).
    """

    def __init__(self, settings: Settings, fetch: FetchJson = fetch_json, clock: Callable[[], float] = time.monotonic):
        self.domain = (settings.authkit_domain or "").lower()
        self.metadata_url = settings.provider_metadata_url
        self._fetch = fetch
        self._clock = clock
        self._endpoint: str | None = None
        self._endpoint_at: float | None = None

    async def _userinfo_endpoint(self) -> str | None:
        now = self._clock()
        if self._endpoint is not None and self._endpoint_at is not None and now - self._endpoint_at < KEY_SET_TTL:
            return self._endpoint
        metadata = await self._fetch(self.metadata_url)
        endpoint = metadata.get("userinfo_endpoint")
        if not isinstance(endpoint, str):
            return None
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != self.domain or parsed.port not in (None, 443):
            log.warning("userinfo_endpoint is not on the provider's host; not calling it")
            return None
        self._endpoint, self._endpoint_at = endpoint, now
        return endpoint

    async def lookup(self, token: str) -> tuple[str | None, str | None]:
        try:
            endpoint = await self._userinfo_endpoint()
            if endpoint is None:
                return None, None
            info = await self._fetch(endpoint, headers={"Authorization": f"Bearer {token}"})
        except Exception as exc:  # noqa: BLE001 - a lookup failure only means the name is unknown
            log.warning("userinfo lookup failed: %s", type(exc).__name__)
            return None, None
        email = info.get("email") if isinstance(info.get("email"), str) else None
        name = info.get("name") if isinstance(info.get("name"), str) else None
        if name is None:
            parts = [info.get(k) for k in ("given_name", "family_name") if isinstance(info.get(k), str)]
            name = " ".join(parts) or None
        return email, name
