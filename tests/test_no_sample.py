"""No sample data (docs/contracts/no-sample.md): the two routes are gone, and nothing in app/ names sample data.

The other decisions are held where their code is tested: the empty Today card in tests/test_web.py, the MCP
results and schemas in tests/test_mcp.py, import and export in tests/test_import.py and tests/test_export.py, the
migration in tests/test_schema.py, and the seed in tests/test_four_weeks.py.
"""

import re

import pytest

from app import services, web
from app.config import ROOT

from .conftest import settings
from .test_web import UNROUTED, WEB, client, only_user, post, seam, sign_in, web_client  # noqa: F401 - fixtures

GONE = ("/settings/sample", "/settings/sample/remove")


def same_page(a, b) -> bool:
    return (a.status_code, a.text, a.headers.get("content-type")) == (b.status_code, b.text, b.headers.get("content-type"))


@pytest.mark.parametrize("path", GONE)
def test_each_gone_route_answers_as_any_unknown_path_does(client, path):
    """Decision 2: signed out, signed in, with the form's token or without, by POST or GET, each answer is the one an
    unknown path gets, and nothing is written."""
    for method in ("POST", "GET"):
        assert same_page(client.request(method, path), client.request(method, UNROUTED)), (method, "signed out")
    sign_in(client)
    for method in ("POST", "GET"):
        assert same_page(client.request(method, path), client.request(method, UNROUTED)), (method, "signed in")
    gone, unknown = post(client, path), post(client, UNROUTED)
    assert gone.status_code == 404 and same_page(gone, unknown)
    assert services.account_empty(only_user().id) == {"empty": True}   # nothing was added


def test_the_routes_and_the_handlers_are_gone():
    paths = {getattr(r, "path", None) for r in web.routes(settings(**WEB))}
    assert not paths & set(GONE) and not any("sample" in str(p) for p in paths)
    for name in ("sample_add_page", "sample_remove_page", "sample_rows_of", "SAMPLE_SUFFIX", "SAMPLE_NOTE"):
        assert not hasattr(web, name), name
    for name in ("sample_state", "add_sample_data", "remove_sample_data", "sample"):
        assert not hasattr(services, name), name
    assert not any(("deleted", "sample") == k or ("saved", "sample") == k for k in web.DAY_STRIPS)


def test_nothing_in_app_names_sample_data():
    """The exit check's grep, as a test: no file under app/ (source, template, stylesheet, picture) says sample."""
    found = []
    for path in sorted((ROOT / "app").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_bytes().decode("utf-8", errors="replace")
            found += [f"{path.relative_to(ROOT)}:{n}" for n, line in enumerate(text.splitlines(), 1)
                      if re.search("sample", line, re.I)]
    assert found == []
    assert not (ROOT / "app" / "templates" / "sample_banner.html").exists()
    assert not (ROOT / "app" / "services" / "sample.py").exists()
