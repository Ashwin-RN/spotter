"""Spotter's settings. This is the only module that reads the environment.

Every setting in stage 1 is a public value. A missing or malformed setting never stops the app
from starting: it leaves the app unconfigured, and /mcp then answers 503 with the reason.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter, ValidationError

# The same URL rules the MCP library applies to its auth settings, so the check below
# predicts exactly what the library will publish.
_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


@dataclass(frozen=True)
class Settings:
    authkit_domain: str | None
    mcp_resource_url: str | None
    auth_debug: bool = False

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
    def problem(self) -> str | None:
        """Why the server cannot check tokens, or None when it can."""
        missing = [name for name, value in (("AUTHKIT_DOMAIN", self.authkit_domain),
                                            ("MCP_RESOURCE_URL", self.mcp_resource_url)) if not value]
        if missing:
            return f"Missing setting: {', '.join(missing)}."
        domain = self.authkit_domain or ""
        if "://" in domain or "/" in domain or urlparse(f"https://{domain}").hostname != domain.lower():
            return "AUTHKIT_DOMAIN must be a bare host name, such as example-name-12.authkit.app."
        resource = self.mcp_resource_url or ""
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


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    return Settings(
        authkit_domain=(env.get("AUTHKIT_DOMAIN") or "").strip() or None,
        mcp_resource_url=(env.get("MCP_RESOURCE_URL") or "").strip() or None,
        auth_debug=(env.get("AUTH_DEBUG") or "").strip() == "1",
    )
