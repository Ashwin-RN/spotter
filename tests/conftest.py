"""Shared test setup. Tests make their own RSA keys, serve their own key set and sign their own tokens.

No test touches the network: every app is built with FakeProvider.fetch in place of the real fetch,
and FakeProvider fails the test if anything asks it for an address outside the fake provider.
"""

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app

DOMAIN = "example.authkit.app"
ISSUER = f"https://{DOMAIN}"
RESOURCE = "https://spotter.example.app/mcp"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class Key:
    def __init__(self, kid: str):
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = self.private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        self.public_pem = self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def jwk(self) -> dict[str, Any]:
        return {**jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True),
                "kid": self.kid, "use": "sig", "alg": "RS256"}


SIGNING_KEY = Key("key-1")
OTHER_KEY = Key("key-2")  # not in the served key set unless a test adds it


def claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {"iss": ISSUER, "aud": RESOURCE, "sub": "user_01TEST", "exp": now + 600, "iat": now,
            "client_id": "client_01TEST", "scope": "openid profile email"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def sign(payload: dict[str, Any] | None = None, key: Key = SIGNING_KEY, kid: str | None = None) -> str:
    return jwt.encode(payload or claims(), key.private_pem, algorithm="RS256", headers={"kid": kid or key.kid})


class FakeProvider:
    """Stands in for the provider: its key set, its metadata and its userinfo endpoint."""

    def __init__(self) -> None:
        self.keys = [SIGNING_KEY]
        self.calls: list[str] = []
        self.sent_headers: list[dict[str, str]] = []
        self.metadata: dict[str, Any] = {"issuer": ISSUER, "userinfo_endpoint": f"{ISSUER}/oauth2/userinfo"}
        self.userinfo: dict[str, Any] | Exception = {"sub": "user_01TEST", "email": "asha@example.com",
                                                     "name": "Asha Test"}
        self.fail_jwks = False

    async def fetch(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append(url)
        self.sent_headers.append(dict(headers or {}))
        if url == f"{ISSUER}/oauth2/jwks":
            if self.fail_jwks:
                raise OSError("provider down")
            return {"keys": [k.jwk() for k in self.keys]}
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            return json.loads(json.dumps(self.metadata))
        if url == f"{ISSUER}/oauth2/userinfo":
            if isinstance(self.userinfo, Exception):
                raise self.userinfo
            return self.userinfo
        raise AssertionError(f"a test fetched an address outside the fake provider: {url}")

    def count(self, suffix: str) -> int:
        return sum(1 for u in self.calls if u.endswith(suffix))


def settings(**overrides: Any) -> Settings:
    values = {"authkit_domain": DOMAIN, "mcp_resource_url": RESOURCE, "auth_debug": False}
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_client(provider):
    """Builds a running app (lifespan started) from settings; closes every one at the end of the test."""
    opened: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        client = TestClient(create_app(settings(**overrides), fetch=provider.fetch))
        client.__enter__()
        opened.append(client)
        return client

    yield make
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def http(make_client) -> TestClient:
    return make_client()


def rpc(client: TestClient, method: str, params: dict | None = None, token: str | None = None, id_: int = 1):
    headers = dict(HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=headers,
                       json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})


def whoami(client: TestClient, token: str) -> dict[str, Any]:
    r = rpc(client, "tools/call", {"name": "whoami", "arguments": {}}, token=token)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]
