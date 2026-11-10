"""The fake sign-in provider the evals run against. Copied from tests/conftest.py, not imported.

Importing tests.conftest would make a temporary directory and two RSA keys at import time and pull in
pytest; the runner needs one key and the fetch function. The app is built with FakeProvider.fetch in
place of the real fetch: the same dependency injection the tests use. Nothing in the app is bypassed:
every call is checked against this key set, issuer and audience as a real token would be.

The key pair lives only in this process. A token signed here is accepted by no other server.
"""

import json
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DOMAIN = "example.authkit.app"
ISSUER = f"https://{DOMAIN}"
RESOURCE = "https://spotter.example.app/mcp"  # the audience tokens are checked against, not where it listens
TOKEN_SECONDS = 1800  # each token is signed just before its run; a run is cut off at 180 s


class Key:
    def __init__(self, kid: str):
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = self.private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    def jwk(self) -> dict[str, Any]:
        return {**jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True),
                "kid": self.kid, "use": "sig", "alg": "RS256"}


def claims(sub: str, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {"iss": ISSUER, "aud": RESOURCE, "sub": sub, "exp": now + TOKEN_SECONDS, "iat": now,
            "client_id": "client_01EVALS", "scope": "openid profile email"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def sign(key: Key, payload: dict[str, Any]) -> str:
    return jwt.encode(payload, key.private_pem, algorithm="RS256", headers={"kid": key.kid})


class FakeProvider:
    """Stands in for the provider: its key set and its metadata. Anything else is refused."""

    def __init__(self, key: Key) -> None:
        self.keys = [key]
        self.openid = {"issuer": ISSUER, "userinfo_endpoint": f"{ISSUER}/oauth2/userinfo"}
        self.metadata = {"issuer": ISSUER, "userinfo_endpoint": None}

    async def fetch(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        if url == f"{ISSUER}/oauth2/jwks":
            return {"keys": [k.jwk() for k in self.keys]}
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return json.loads(json.dumps(self.openid))
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            return json.loads(json.dumps(self.metadata))
        # Every eval person is created with an email and a name, so userinfo is never needed.
        raise OSError(f"the eval provider does not serve {url}")
