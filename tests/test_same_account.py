"""One person, one account, from either side: a web sign-in and an MCP token with the same subject
reach the same user row, in either order. The sign-in id on /settings equals whoami's user_id, and the
account id on /settings equals whoami's account_id.
"""

import re

from .conftest import claims, sign
from .test_mcp import ok
from .test_web import FakeSeam, only_user, seam, sign_in, user_count, web_client  # noqa: F401 - fixtures

WEB_SUBJECT = "user_01WEB"  # FakeSeam's identity


def settings_ids(client) -> tuple[str, int]:
    """The sign-in id and the account id, as the Settings page shows them."""
    page = client.get("/settings")
    assert page.status_code == 200, page.text
    found = dict(re.findall(r"<dt>(Sign-in id|Account id)</dt><dd><code>([^<]+)</code></dd>", page.text))
    return found["Sign-in id"], int(found["Account id"])


def test_web_first_then_mcp_is_one_account(web_client, seam: FakeSeam):
    client = web_client()
    assert sign_in(client).status_code == 303
    assert user_count() == 1
    web_subject, web_account = settings_ids(client)
    assert web_subject == WEB_SUBJECT

    who = ok(client, sign(claims(sub=WEB_SUBJECT)), "whoami")
    assert user_count() == 1
    assert (who["user_id"], who["account_id"]) == (web_subject, web_account)
    assert (who["email"], who["name"]) == ("asha@example.com", "Asha Web")  # what the web sign-in stored
    assert only_user().id == web_account


def test_mcp_first_then_web_is_one_account(web_client, seam: FakeSeam):
    client = web_client()
    who = ok(client, sign(claims(sub=WEB_SUBJECT)), "whoami")
    assert user_count() == 1

    assert sign_in(client).status_code == 303
    assert user_count() == 1
    assert settings_ids(client) == (who["user_id"], who["account_id"])
    assert (only_user().email, only_user().name) == ("asha@example.com", "Asha Web")  # the web filled them in


def test_a_different_subject_is_a_different_account(web_client, seam: FakeSeam):
    client = web_client()
    sign_in(client)
    who = ok(client, sign(claims(sub="user_01SOMEONE_ELSE")), "whoami")
    assert user_count() == 2
    assert settings_ids(client)[1] != who["account_id"]
