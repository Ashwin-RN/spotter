"""The hand-written CSRF check, on a small app of its own: one token per session, a hidden form field,
compared in constant time on every non-GET request, 403 on a miss."""

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app import csrf


async def show_token(request: Request) -> PlainTextResponse:
    return PlainTextResponse(csrf.token(request))


async def change(request: Request) -> PlainTextResponse:
    form = await request.form()  # the endpoint can still read the form the check already parsed
    return PlainTextResponse(f"changed {form.get('value')}")


def refuse(request: Request) -> PlainTextResponse:
    return PlainTextResponse("refused", status_code=403)


def build(with_sessions: bool = True) -> TestClient:
    middleware = [Middleware(SessionMiddleware, secret_key="test-only-key")] if with_sessions else []
    app = Starlette(routes=[
        Route("/token", show_token),
        Route("/change", csrf.protect(change, refuse), methods=["GET", "POST", "PUT", "DELETE"]),
    ], middleware=middleware)
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return build()


def test_the_token_is_random_long_and_stable_within_a_session(client):
    first = client.get("/token").text
    assert len(first) >= 40 and client.get("/token").text == first
    other = build().get("/token").text
    assert other != first


def test_a_post_with_the_token_goes_through(client):
    token = client.get("/token").text
    r = client.post("/change", data={csrf.FIELD: token, "value": "x"})
    assert r.status_code == 200 and r.text == "changed x"


@pytest.mark.parametrize("sent", [None, "", "wrong", "é", "x" * 10_000], ids=["missing", "empty", "wrong",
                                                                             "non-ascii", "long"])
def test_a_post_without_the_right_token_is_403(client, sent):
    client.get("/token")
    data = {"value": "x"} if sent is None else {"value": "x", csrf.FIELD: sent}
    r = client.post("/change", data=data)
    assert r.status_code == 403 and r.text == "refused"


def test_a_post_before_any_token_exists_is_403(client):
    assert client.post("/change", data={csrf.FIELD: "", "value": "x"}).status_code == 403


def test_the_token_in_a_query_string_does_not_count(client):
    token = client.get("/token").text
    assert client.post(f"/change?{csrf.FIELD}={token}", data={"value": "x"}).status_code == 403


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_every_non_get_method_is_checked(client, method):
    client.get("/token")
    assert client.request(method, "/change").status_code == 403


def test_get_is_not_checked(client):
    assert client.get("/change").status_code == 200


def test_without_sessions_every_post_is_refused():
    client = build(with_sessions=False)
    assert client.post("/change", data={csrf.FIELD: "anything"}).status_code == 403


@pytest.mark.parametrize("sent, expected, result", [
    ("abc", "abc", True),
    ("abc", "abd", False),
    ("", "", False),
    (None, None, False),
    ("abc", None, False),
    (None, "abc", False),
    ("é", "é", True),
    ("é", "e", False),
    (["abc"], "abc", False),
])
def test_same(sent, expected, result):
    assert csrf.same(sent, expected) is result
