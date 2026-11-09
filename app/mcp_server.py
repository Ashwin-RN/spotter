"""Spotter's MCP server. Stage 1 has one function, whoami, which names the signed-in person.

The MCP library does the sign-in work: it serves the protected-resource metadata, answers 401 with
`resource_metadata`, and runs every call through the token verifier. whoami reads the verified token
the library stored for this request; it never reads the Authorization header itself.

Scopes: the metadata advertises SCOPES, and a client asks the provider for what is advertised there
(the library's challenges carry no `scope=` of their own). The server requires only REQUIRED_SCOPES.
"""

from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.routing import Route

from app.auth import JwksTokenVerifier, UserinfoClient
from app.config import Settings

INSTRUCTIONS = (
    "Spotter is a personal fitness record. This build is a sign-in test: call whoami to see "
    "which account is signed in."
)

# What clients are told to ask for. offline_access is what gets a client a refresh token.
SCOPES = ["openid", "profile", "email", "offline_access"]
# What a token must carry to get in: openid, without which the provider's userinfo cannot name the person.
REQUIRED_SCOPES = ["openid"]

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


class WhoAmI(BaseModel):
    user_id: str = Field(description="The provider's stable id for the signed-in person (the token's sub)")
    email: str | None = Field(description="Their email address, or null when the provider does not say")
    name: str | None = Field(description="Their name, or null when the provider does not say")
    scopes: list[str] = Field(description="The scopes the token carries")
    client_id: str = Field(description="The app the person signed in through")
    issuer: str = Field(description="Who signed the token")
    expires_at: int = Field(description="When the token expires, in seconds since 1970 (UTC)")


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


def build_server(settings: Settings, verifier: JwksTokenVerifier, userinfo: UserinfoClient) -> MCPServer:
    """A fresh server per app: the library's session manager can be started only once."""
    server = SpotterServer(
        name="Spotter",
        version="0.1",
        instructions=INSTRUCTIONS,
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

    @server.tool(
        name="whoami",
        title="Who am I",
        description="Says who is signed in to Spotter: their id, email and name, and what the sign-in allows.",
        annotations=READ_ONLY,
    )
    async def whoami() -> WhoAmI:
        token = get_access_token()
        if token is None or token.subject is None:  # the library refuses unsigned calls before this runs
            raise RuntimeError("No verified sign-in on this call.")
        claims = token.claims or {}
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        name = claims.get("name") if isinstance(claims.get("name"), str) else None
        if email is None or name is None:
            found_email, found_name = await userinfo.lookup(token.token, token.subject)
            email, name = email or found_email, name or found_name
        return WhoAmI(
            user_id=token.subject,
            email=email,
            name=name,
            scopes=token.scopes,
            client_id=token.client_id,
            issuer=str(claims.get("iss")),
            expires_at=token.expires_at or 0,
        )

    return server
