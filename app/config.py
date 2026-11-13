"""Spotter's settings. This is the only module that reads the environment.

A missing or malformed setting never stops the app from starting. Without the sign-in settings the
app is unconfigured and /mcp answers 503 with the reason. Without the WorkOS settings web sign-in is
off and MCP still works.

Secret values (DATABASE_URL, WORKOS_API_KEY, SESSION_SECRET, USDA_API_KEY) are never part of a repr
and are never logged. Without USDA_API_KEY the USDA food lookup is off and everything else works. get_settings() is how the rest of the app reads them; tests swap them with use_settings().

DB_POOL_SIZE and DB_MAX_OVERFLOW size the Postgres connection pool (app/db.py); unset or out of range, the
defaults hold.

OWNER_USER_ID names the one account that may read other people's calls (owner_calls): the sign-in id whoami
gives. Unset, nobody is the owner.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parent.parent
# Unset DATABASE_URL: a local SQLite file in the repo root (gitignored). Only for a laptop or the tests:
# on a public https site an unset DATABASE_URL is a configuration error (Settings.database_problem).
DEFAULT_DATABASE_URL = f"sqlite:///{(ROOT / 'spotter.db').as_posix()}"
DEFAULT_AUTH_PROVIDER = "workos"
DEFAULT_SIGNUPS_PER_DAY = 50
# The Postgres connection pool, per process: kept connections and extra ones on demand. Ten in all stays well
# under a stock Postgres's 100. DB_POOL_SIZE and DB_MAX_OVERFLOW override them within these bounds; any other
# value falls back to the default. SQLite ignores both (app/db.py).
DEFAULT_DB_POOL_SIZE = 5
DEFAULT_DB_MAX_OVERFLOW = 5
DB_POOL_SIZE_RANGE = (1, 50)
DB_MAX_OVERFLOW_RANGE = (0, 50)
# The fixed label for deriving the session key from WORKOS_API_KEY. Changing it signs everyone out.
SESSION_KEY_LABEL = b"spotter session cookie v1"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
NO_DATABASE = ("DATABASE_URL is not set. A public https site never falls back to a local SQLite file: "
               "set DATABASE_URL to the Postgres address.")

# The same URL rules the MCP library applies to its auth settings, so the check below
# predicts exactly what the library will publish.
_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))

# A lowercase DNS host name: dot-separated labels of letters, digits and inner hyphens.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*")


@dataclass(frozen=True)
class Settings:
    authkit_domain: str | None
    mcp_resource_url: str | None
    auth_debug: bool = False
    database_url: str = field(default=DEFAULT_DATABASE_URL, repr=False)  # may carry a password
    workos_client_id: str | None = None
    workos_api_key: str | None = field(default=None, repr=False)
    session_secret: str | None = field(default=None, repr=False)
    auth_provider: str = DEFAULT_AUTH_PROVIDER
    signups_per_day: int = DEFAULT_SIGNUPS_PER_DAY
    usda_api_key: str | None = field(default=None, repr=False)  # unset: USDA lookup is off
    db_pool_size: int = DEFAULT_DB_POOL_SIZE  # Postgres only
    db_max_overflow: int = DEFAULT_DB_MAX_OVERFLOW  # Postgres only
    owner_user_id: str | None = None  # the sign-in id of the one account that may read others' calls; unset: none

    @property
    def site_origin(self) -> str | None:
        """Scheme and host of MCP_RESOURCE_URL: the base of every web link. Never taken from a request."""
        parsed = urlparse(self.mcp_resource_url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def database_problem(self) -> str | None:
        """Why the database cannot be used, or None when it can. On a public https site (not localhost)
        the local SQLite fallback would be a throwaway file on the host, so it is refused."""
        if self.database_url != DEFAULT_DATABASE_URL:
            return None
        parsed = urlparse(self.site_origin or "")
        if parsed.scheme == "https" and (parsed.hostname or "") not in LOCAL_HOSTS:
            return NO_DATABASE
        return None

    @property
    def web_sign_in_configured(self) -> bool:
        """Web sign-in needs both WorkOS values. Without them the web pages say so and fail closed."""
        return bool(self.workos_api_key and self.workos_client_id)

    @property
    def session_key(self) -> str | None:
        """The key that signs the session cookie: SESSION_SECRET when set, else derived from
        WORKOS_API_KEY with HKDF-SHA256 and a fixed label, else None (no web sessions)."""
        if self.session_secret:
            return self.session_secret
        if not self.workos_api_key:
            return None
        derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=SESSION_KEY_LABEL)
        return derived.derive(self.workos_api_key.encode()).hex()

    @property
    def issuer(self) -> str:
        return f"https://{self.authkit_domain}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/oauth2/jwks"

    @property
    def provider_metadata_url(self) -> str:
        return f"{self.issuer}/.well-known/oauth-authorization-server"

    @property
    def openid_configuration_url(self) -> str:
        return f"{self.issuer}/.well-known/openid-configuration"

    @property
    def problem(self) -> str | None:
        """Why the server cannot check tokens, or None when it can."""
        missing = [name for name, value in (("AUTHKIT_DOMAIN", self.authkit_domain),
                                            ("MCP_RESOURCE_URL", self.mcp_resource_url)) if not value]
        if missing:
            return f"Missing setting: {', '.join(missing)}."
        if not _HOST.fullmatch(self.authkit_domain or ""):
            return "AUTHKIT_DOMAIN must be a bare lowercase host name, such as example-name-12.authkit.app."
        resource = self.mcp_resource_url or ""
        parsed = urlparse(resource)
        if "?" in resource or "#" in resource or parsed.query or parsed.fragment:
            return "MCP_RESOURCE_URL must not carry a ?query or a #fragment."
        try:
            published = str(_URL.validate_python(resource))
        except ValidationError:
            return "MCP_RESOURCE_URL is not an http(s) address."
        if published != resource:
            # The metadata would publish a different string from the one tokens are checked against.
            return f"MCP_RESOURCE_URL would be published as {published!r}, not as written."
        return None

    @property
    def configured(self) -> bool:
        return self.problem is None


def database_address(url: str | None) -> str:
    """The SQLAlchemy address for DATABASE_URL. Unset gives the local SQLite file. The host's
    postgres:// and postgresql:// forms, in any case, are given the psycopg 3 driver."""
    url = (url or "").strip()
    if not url:
        return DEFAULT_DATABASE_URL
    scheme, separator, rest = url.partition("://")
    if separator and scheme.lower() in ("postgres", "postgresql"):
        return "postgresql+psycopg://" + rest
    return url


def _signups_per_day(value: str | None) -> int:
    try:
        number = int((value or "").strip())
    except ValueError:
        return DEFAULT_SIGNUPS_PER_DAY
    return number if number >= 0 else DEFAULT_SIGNUPS_PER_DAY


def _whole_number(value: str | None, bounds: tuple[int, int], default: int) -> int:
    """A whole number within bounds (both ends included), else the default: unset, a fraction, a word or a
    number out of range all fall back, so a typo on the host never stops the app or empties the pool."""
    text = (value or "").strip()
    if not (text.isascii() and text.isdigit()):
        return default
    number = int(text)
    return number if bounds[0] <= number <= bounds[1] else default


def _text(env: Mapping[str, str], name: str) -> str | None:
    return (env.get(name) or "").strip() or None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    return Settings(
        # Host names are case-insensitive; the library publishes the lowercase form, so compare with that.
        authkit_domain=(env.get("AUTHKIT_DOMAIN") or "").strip().lower() or None,
        mcp_resource_url=_text(env, "MCP_RESOURCE_URL"),
        auth_debug=(env.get("AUTH_DEBUG") or "").strip() == "1",
        database_url=database_address(env.get("DATABASE_URL")),
        workos_client_id=_text(env, "WORKOS_CLIENT_ID"),
        workos_api_key=_text(env, "WORKOS_API_KEY"),
        session_secret=_text(env, "SESSION_SECRET"),
        auth_provider=_text(env, "AUTH_PROVIDER") or DEFAULT_AUTH_PROVIDER,
        signups_per_day=_signups_per_day(env.get("SIGNUPS_PER_DAY")),
        usda_api_key=_text(env, "USDA_API_KEY"),
        db_pool_size=_whole_number(env.get("DB_POOL_SIZE"), DB_POOL_SIZE_RANGE, DEFAULT_DB_POOL_SIZE),
        db_max_overflow=_whole_number(env.get("DB_MAX_OVERFLOW"), DB_MAX_OVERFLOW_RANGE, DEFAULT_DB_MAX_OVERFLOW),
        owner_user_id=_text(env, "OWNER_USER_ID"),
    )


_current: Settings | None = None


def get_settings() -> Settings:
    """The settings the running app uses, read from the environment once."""
    global _current
    if _current is None:
        _current = load_settings()
    return _current


def use_settings(settings: Settings | None) -> None:
    """Replace the running settings (tests, and the app factory). None reads the environment again
    on the next get_settings()."""
    global _current
    _current = settings
